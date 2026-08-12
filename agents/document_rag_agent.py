"""
agents/document_rag_agent.py
──────────────────────────────
Document RAG agent — the core agent of this build.

Full pipeline:
  1. Load user attributes from PostgreSQL
  2. PDP evaluation (access control decision)
  3. PEP filter construction (Qdrant filter)
  4. Hybrid retrieval (BM25 + dense via RRF)
  5. Cross-encoder reranking
  6. Citation building
  7. LLM call with context-injected prompt
  8. Tool-calling loop for chart/export intents (Week 5)
  9. Append to Redis conversation history

Returns a structured response with the answer and citations.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from access_control.intent_classifier import QueryIntent
from access_control.pdp import PDPDecision, PDPResult, UserAttributes, evaluate as pdp_evaluate
from access_control.pep import build_qdrant_filter
from agents.tool_executor import ToolExecutor
from db.models import User
from retrieval.citation_builder import Citation, build_citations, build_context_block
from retrieval.hybrid_retrieval import hybrid_search
from retrieval.reranker import rerank
from services.llm_client import chat_completion, extract_text_response, extract_tool_calls
from services.redis_service import append_message, get_history

logger = structlog.get_logger(__name__)

RAG_SYSTEM_PROMPT = """You are Sparkline AI, an in-house enterprise assistant for Sparkline,
a construction and equipment company. You answer questions based ONLY on the provided source
documents. Do not fabricate information or use knowledge outside the provided context.

Rules:
1. Base your answer strictly on the SOURCE passages provided below.
2. If the answer is not found in the sources, say: "I couldn't find this in the available documents."
3. Always cite the source document and page number when referencing specific facts.
4. Be concise and professional.
5. For numerical data (financials, measurements), quote them exactly as they appear in the source."""

BLENDED_SYSTEM_PROMPT = """You are Sparkline AI, an in-house enterprise assistant for Sparkline,
a construction and equipment company.

Source passages from Sparkline's documents are provided below, but they may only partly cover the
question — or not cover it at all. Answer the user's question properly either way.

Rules:
1. If the sources answer the question, use them and cite the document and page number.
2. If the sources do not cover it, answer from your own general knowledge instead — do NOT refuse,
   and do NOT tell the user the information is missing from the documents.
3. When you answer from general knowledge rather than the sources, say so in one short sentence
   (e.g. "This isn't from a Sparkline document, but generally...") so the user knows the difference.
4. Never present general knowledge as if it came from a Sparkline document.
5. Be concise and professional.
6. For numerical data (financials, measurements), quote them exactly as they appear in the source."""

RAG_USER_TEMPLATE = """{context_block}

---
Question: {question}

Based on the sources above, please provide a clear and accurate answer."""


@dataclass
class RetrievalResult:
    """
    Everything the retrieval half of the pipeline produced.

    Exists so the router can look at retrieval quality — specifically top_score —
    before committing to answering from documents. Without this split the router
    would have to guess from the query wording alone, which is exactly the
    approach that made the general agent unreachable.
    """

    chunks: list[dict]
    citations: list[Citation]
    context_block: str
    pdp_decision: str
    top_score: float | None = None
    denied: bool = False
    error: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.chunks) and self.top_score is not None


class DocumentRAGAgent:
    """Full RAG pipeline agent with PDP/PEP access control, hybrid retrieval, and citations."""

    def __init__(self) -> None:
        self._tool_executor = ToolExecutor()

    async def retrieve(
        self,
        query: str,
        user_id: str,
        intent: QueryIntent,
        db: AsyncSession,
        retrieval_query: str | None = None,
    ) -> RetrievalResult:
        """
        Run the access-controlled retrieval half of the pipeline.

        Args:
            query: The user's own words (used for citations/logging)
            retrieval_query: What to actually search with. Differs from `query`
                only for condensed follow-ups; defaults to `query`.

        Returns a RetrievalResult; callers must check `.denied` and `.error`.
        """
        log = logger.bind(user_id=user_id, intent=intent.value)
        search_text = retrieval_query or query

        # ── Step 1: Load user attributes ──────────────────────────
        user_attrs = await self._load_user(user_id, db)
        if user_attrs is None:
            log.error("rag_agent.user_not_found")
            return RetrievalResult(
                chunks=[], citations=[], context_block="",
                pdp_decision="error", error="User not found",
            )

        # ── Step 2: PDP evaluation ────────────────────────────────
        pdp_result: PDPResult = pdp_evaluate(user=user_attrs, intent=intent)
        log.info("rag_agent.pdp", decision=pdp_result.decision.value, reason=pdp_result.reason)

        if pdp_result.decision == PDPDecision.DENY:
            return RetrievalResult(
                chunks=[], citations=[], context_block="",
                pdp_decision="deny", denied=True,
            )

        # ── Step 3: PEP → Qdrant filter ───────────────────────────
        qdrant_filter = build_qdrant_filter(pdp_result)

        # ── Step 4: Hybrid retrieval ──────────────────────────────
        raw_chunks = hybrid_search(query=search_text, qdrant_filter=qdrant_filter)
        log.info("rag_agent.retrieval", chunk_count=len(raw_chunks))

        if not raw_chunks:
            return RetrievalResult(
                chunks=[], citations=[], context_block="",
                pdp_decision=pdp_result.decision.value,
            )

        # ── Step 5: Reranking ─────────────────────────────────────
        reranked = rerank(query=search_text, candidates=raw_chunks)
        top_score = reranked[0]["rerank_score"] if reranked else None
        log.info("rag_agent.reranked", final_count=len(reranked), top_score=top_score)

        # ── Step 6: Citations ─────────────────────────────────────
        citations: list[Citation] = build_citations(reranked)
        context_block = build_context_block(reranked, citations)

        return RetrievalResult(
            chunks=reranked,
            citations=citations,
            context_block=context_block,
            pdp_decision=pdp_result.decision.value,
            top_score=top_score,
        )

    async def answer(
        self,
        query: str,
        session_id: str,
        intent: QueryIntent,
        retrieval: RetrievalResult,
        blended: bool = False,
        record_history: bool = True,
    ) -> dict:
        """
        Turn a RetrievalResult into an answer.

        Args:
            blended: Use the prompt that permits general knowledge alongside the
                sources (for mid-confidence retrieval) instead of the strict
                grounded-only prompt.
            record_history: Append this turn to the Redis session history. The
                router sets this False when the answer may still be discarded in
                favour of a general-knowledge fallback, so a rejected answer
                never lands in the conversation the next turn reads back.
        """
        log = logger.bind(intent=intent.value, blended=blended)

        # ── Step 7: Build prompt + LLM call ─────────────────────
        history = await get_history(session_id, max_turns=6)
        user_message = RAG_USER_TEMPLATE.format(
            context_block=retrieval.context_block,
            question=query,
        )

        system_prompt = BLENDED_SYSTEM_PROMPT if blended else RAG_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        citations = retrieval.citations
        reranked = retrieval.chunks

        # ── Step 8: Tool-calling loop (chart/export intents) ─────
        tool_outputs: list[dict] = []
        if intent in (QueryIntent.CHART_REQUEST, QueryIntent.EXPORT_REQUEST):
            tools = self._tool_executor.get_tool_definitions(intent)
            completion = await chat_completion(messages=messages, tools=tools)
            tool_calls = extract_tool_calls(completion)

            if tool_calls:
                tool_outputs = await self._tool_executor.execute_tool_calls(
                    tool_calls=tool_calls,
                    context_chunks=reranked,
                )
                # Add tool results back into the conversation and get final answer
                messages.append(completion["choices"][0]["message"])
                for tool_out in tool_outputs:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_out["tool_call_id"],
                        "content": tool_out.get("result_summary", "Done"),
                    })
                final_completion = await chat_completion(messages=messages)
                answer = extract_text_response(final_completion)
            else:
                answer = extract_text_response(completion)
        else:
            completion = await chat_completion(messages=messages)
            answer = extract_text_response(completion)

        # ── Step 9: Update Redis history ─────────────────────────
        if record_history:
            await append_message(session_id, "user", query)
            await append_message(session_id, "assistant", answer)

        log.info("rag_agent.complete", answer_length=len(answer), citations=len(citations))

        return {
            "answer": answer,
            "citations": [c.to_dict() for c in citations],
            "agent_type": "document_rag_blended" if blended else "document_rag",
            "intent": intent.value,
            "pdp_decision": retrieval.pdp_decision,
            "tool_outputs": tool_outputs,
            "top_rerank_score": retrieval.top_score,
        }

    async def handle(
        self,
        query: str,
        user_id: str,
        session_id: str,
        intent: QueryIntent,
        db: AsyncSession,
    ) -> dict:
        """
        Retrieve and answer in one call — the original entry point.

        Kept for the tool-calling (chart/export) path and for ROUTER_MODE=legacy,
        both of which always want the document pipeline regardless of retrieval
        scores. The evidence-gated path in QueryRouter calls retrieve() and
        answer() separately so it can inspect the score in between.
        """
        retrieval = await self.retrieve(query=query, user_id=user_id, intent=intent, db=db)

        if retrieval.error:
            return self._error_response(retrieval.error, intent)

        if retrieval.denied:
            return {
                "answer": "Access denied. You don't have permission to access this resource.",
                "citations": [],
                "agent_type": "document_rag",
                "intent": intent.value,
                "pdp_decision": "deny",
                "error": "access_denied",
            }

        if not retrieval.chunks:
            return {
                "answer": "I couldn't find any relevant documents for your query.",
                "citations": [],
                "agent_type": "document_rag",
                "intent": intent.value,
                "pdp_decision": retrieval.pdp_decision,
            }

        return await self.answer(
            query=query,
            session_id=session_id,
            intent=intent,
            retrieval=retrieval,
        )

    async def _load_user(self, user_id: str, db: AsyncSession) -> UserAttributes | None:
        """Load user attributes from PostgreSQL."""
        try:
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                return None
            return UserAttributes(
                user_id=str(user.id),
                username=user.username,
                department=user.department,
                designation=user.designation,
                default_role=user.default_role,
                is_active=user.is_active,
                is_admin=user.is_admin,
                is_file_admin=user.is_file_admin,
            )
        except Exception as e:
            logger.error("rag_agent.load_user_failed", user_id=user_id, error=str(e))
            return None

    @staticmethod
    def _error_response(reason: str, intent: QueryIntent) -> dict:
        return {
            "answer": f"An error occurred: {reason}",
            "citations": [],
            "agent_type": "document_rag",
            "intent": intent.value,
            "pdp_decision": "error",
            "error": reason,
        }
