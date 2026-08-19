"""
router/query_router.py
───────────────────────
Two-step query router (plain Python — no LangChain/LlamaIndex/LangGraph).

Step 1: Deterministic rules — intent classification plus the pre-route check.
Step 2: Evidence gate — retrieve first, then decide from the retrieval scores
        whether the answer should come from documents or general knowledge.

Why the gate exists
───────────────────
Users are not expected to know or care which mode answers them; there is one
model in the chat UI and routing is invisible. That rules out asking the user,
and keyword matching cannot make the call either — "what is the standard
warranty period" is a document question only if the warranty policy happens to
be in the corpus. The words never say; the corpus does. So retrieval runs first
and the cross-encoder rerank score of the best chunk decides:

    score >= ROUTER_RAG_SCORE_HIGH   → DocumentRAGAgent, strict grounded prompt
    LOW <= score < HIGH              → DocumentRAGAgent, blended prompt
    score < ROUTER_RAG_SCORE_LOW     → GeneralLLMAgent, no context, no citations

Retrieval is local (embedding + Qdrant + BM25 + cross-encoder) and runs on every
query anyway in the document case, so the gate costs nothing extra when it
routes to documents, and one wasted local retrieval when it routes to general.

Routing table:
  - CHART_REQUEST / EXPORT_REQUEST
      → DocumentRAGAgent (tool-calling needs document context; no gate)
  - DOCUMENT_QA / DOCUMENT_SEARCH with explicit document language
      → DocumentRAGAgent (user named a source; honour it, no gate)
  - Small talk / identity / meta
      → GeneralLLMAgent (no retrieval at all)
  - Everything else
      → evidence gate decides
  - ADMIN
      → Rejected here with 403 if called via chat endpoint;
        admin routes are separate endpoints in gateway/routes/admin.py

Set ROUTER_MODE=legacy in .env to restore the pre-gate behaviour (every query to
the RAG agent) without a code change.
"""

from __future__ import annotations

import structlog

from access_control.intent_classifier import QueryIntent, classify_intent
from agents.document_rag_agent import DocumentRAGAgent
from agents.general_llm_agent import GeneralLLMAgent
from config.settings import get_settings
from retrieval.session_merge import SessionContext, has_session_evidence
from router.route_decision import Route, build_retrieval_query, is_refusal, pre_route
from services.redis_service import (
    append_message,
    get_last_document_query,
    set_last_document_query,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

_DOCUMENT_INTENTS = (
    QueryIntent.DOCUMENT_QA,
    QueryIntent.DOCUMENT_SEARCH,
    QueryIntent.CHART_REQUEST,
    QueryIntent.EXPORT_REQUEST,
)

# Tool-calling needs document context to work from, so these skip the gate.
_TOOL_INTENTS = (QueryIntent.CHART_REQUEST, QueryIntent.EXPORT_REQUEST)


class QueryRouter:
    """
    Routes incoming queries to the correct agent based on rules plus retrieval
    evidence. Both agents are instantiated lazily and cached for the lifetime of
    the router.
    """

    def __init__(self) -> None:
        self._rag_agent = DocumentRAGAgent()
        self._general_agent = GeneralLLMAgent()

    async def route(
        self,
        query: str,
        user_id: str,
        session_id: str,
        db,
        chat_id: str | None = None,
    ) -> dict:
        """
        Classify the query and dispatch to the appropriate agent.

        Args:
            query: Raw user query string
            user_id: Authenticated user UUID string
            session_id: Redis session ID for conversation history
            db: AsyncSession (passed through to agents that need DB access)

        Returns:
            Agent response dict (see AgentResponse format in each agent)
        """
        intent = classify_intent(query)
        log = logger.bind(user_id=user_id, intent=intent.value)

        if intent == QueryIntent.ADMIN:
            # Admin intents must go through dedicated admin endpoints, not chat
            log.warning("router.admin_intent_via_chat")
            return {
                "answer": "Administrative operations must be performed via the admin interface.",
                "citations": [],
                "agent_type": "router",
                "intent": intent.value,
                "error": "admin_via_chat",
            }

        # ── Legacy mode: pre-gate behaviour, everything to RAG ────────
        if settings.router_mode == "legacy":
            log.info("router.dispatch.document_rag", mode="legacy")
            return await self._rag_agent.handle(
                query=query, user_id=user_id, session_id=session_id, intent=intent, db=db,
            )

        # ── Rules that decide without retrieval ───────────────────────
        hint = pre_route(query)
        log = log.bind(pre_route=hint.value)

        if hint == Route.GENERAL:
            log.info("router.dispatch.general_llm", reason="pre_route_rule")
            return await self._general_agent.handle(
                query=query, user_id=user_id, session_id=session_id,
            )

        if intent in _TOOL_INTENTS:
            log.info("router.dispatch.document_rag", reason="tool_intent")
            tool_response = await self._rag_agent.handle(
                query=query, user_id=user_id, session_id=session_id, intent=intent, db=db,
            )
            await set_last_document_query(session_id, query, tool_response.get("answer", ""))
            return tool_response

        # ── Evidence gate ─────────────────────────────────────────────
        # Anything the rules didn't settle: retrieve, then judge on the score.
        gate_intent = intent if intent in _DOCUMENT_INTENTS else QueryIntent.DOCUMENT_QA

        retrieval_query = query
        if settings.router_condense_followups:
            anchor = await get_last_document_query(session_id)
            retrieval_query = build_retrieval_query(query, anchor)
            if retrieval_query != query:
                log.info("router.followup_condensed", retrieval_query=retrieval_query)

        # Attachments for this chat, if any. Built unconditionally when a chat
        # ID is known — the lookup is cheap and returns nothing for the common
        # case of a chat with no attachments.
        session_ctx = (
            SessionContext(chat_id=chat_id, user_id=user_id) if chat_id else None
        )

        retrieval = await self._rag_agent.retrieve(
            query=query,
            user_id=user_id,
            intent=gate_intent,
            db=db,
            retrieval_query=retrieval_query,
            session_ctx=session_ctx,
        )

        if retrieval.error:
            log.error("router.retrieval_error", error=retrieval.error)
            return {
                "answer": f"An error occurred: {retrieval.error}",
                "citations": [],
                "agent_type": "document_rag",
                "intent": gate_intent.value,
                "pdp_decision": "error",
                "error": retrieval.error,
            }

        if retrieval.denied:
            log.warning("router.access_denied")
            return {
                "answer": "Access denied. You don't have permission to access this resource.",
                "citations": [],
                "agent_type": "document_rag",
                "intent": gate_intent.value,
                "pdp_decision": "deny",
                "error": "access_denied",
            }

        # Nothing retrievable at all (empty corpus, or everything filtered out by
        # access control) — general knowledge is the only thing left to offer.
        if not retrieval.has_evidence:
            log.info("router.dispatch.general_llm", reason="no_retrievable_chunks")
            return await self._general_agent.handle(
                query=query, user_id=user_id, session_id=session_id,
            )

        score = retrieval.top_score
        log = log.bind(top_rerank_score=round(score, 3))

        # The user named a document, so answer from documents whatever the score.
        # "We searched and the documents don't say" is a legitimate answer to
        # "what does the policy say about X" — unlike for an unmarked question,
        # where it's just a dead end.
        if hint == Route.DOCUMENTS:
            log.info("router.dispatch.document_rag", reason="explicit_document_request")
            explicit = await self._rag_agent.answer(
                query=query, session_id=session_id, intent=gate_intent, retrieval=retrieval,
            )
            await set_last_document_query(session_id, query, explicit.get("answer", ""))
            return explicit

        # A file the user attached to this chat is in the results. Attaching a
        # file is as explicit a document request as naming one, so the score
        # floor does not apply — otherwise "summarise this", which retrieves
        # poorly by construction, would be answered from general knowledge and
        # look like the attachment had been ignored.
        if has_session_evidence(retrieval.chunks):
            log.info("router.dispatch.document_rag", reason="session_attachment")
            attached = await self._rag_agent.answer(
                query=query, session_id=session_id, intent=gate_intent,
                retrieval=retrieval,
            )
            await set_last_document_query(session_id, query, attached.get("answer", ""))
            return attached

        if score < settings.router_rag_score_low:
            log.info("router.dispatch.general_llm", reason="below_score_floor")
            return await self._general_agent.handle(
                query=query, user_id=user_id, session_id=session_id,
            )

        blended = score < settings.router_rag_score_high
        log.info(
            "router.dispatch.document_rag",
            reason="blended_band" if blended else "confident_document_hit",
        )
        # History is written by the router rather than the agent here: a refused
        # answer may still be replaced below, and it must not be left behind in
        # the session history for the next turn to read back as context.
        response = await self._rag_agent.answer(
            query=query,
            session_id=session_id,
            intent=gate_intent,
            retrieval=retrieval,
            blended=blended,
            record_history=False,
        )

        # ── Safety net ────────────────────────────────────────────────
        # The gate let this through but the model still refused. Rather than
        # hand the user a dead end, answer the question from general knowledge.
        if settings.router_enable_general_fallback and is_refusal(response.get("answer", "")):
            log.info("router.general_fallback", reason="rag_refused_despite_gate")
            fallback = await self._general_agent.handle(
                query=query, user_id=user_id, session_id=session_id,
            )
            fallback["agent_type"] = "general_fallback"
            return fallback

        # Anchor future follow-ups on this exchange, now that it is known to have
        # been answered from documents and not replaced by the fallback.
        await set_last_document_query(session_id, query, response.get("answer", ""))

        await append_message(session_id, "user", query)
        await append_message(session_id, "assistant", response.get("answer", ""))
        return response
