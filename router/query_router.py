"""
router/query_router.py
───────────────────────
Two-step query router (plain Python — no LangChain/LlamaIndex/LangGraph).

Step 1: Classify the query intent using the intent classifier.
Step 2: Dispatch to the appropriate agent.

Routing table:
  - CHART_REQUEST / EXPORT_REQUEST / DOCUMENT_QA / DOCUMENT_SEARCH
      → DocumentRAGAgent (handles all document-related intents,
        including tool-calling for charts and exports)
  - GENERAL_QUERY
      → GeneralLLMAgent (passthrough to LLM without retrieval)
  - ADMIN
      → Rejected here with 403 if called via chat endpoint;
        admin routes are separate endpoints in gateway/routes/admin.py

The router is synchronous in classification and async in dispatch.
"""

from __future__ import annotations

import structlog

from access_control.intent_classifier import QueryIntent, classify_intent
from agents.document_rag_agent import DocumentRAGAgent
from agents.general_llm_agent import GeneralLLMAgent

logger = structlog.get_logger(__name__)


class QueryRouter:
    """
    Routes incoming queries to the correct agent based on classified intent.
    Both agents are instantiated lazily and cached for the lifetime of the router.
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

        if intent in (
            QueryIntent.DOCUMENT_QA,
            QueryIntent.DOCUMENT_SEARCH,
            QueryIntent.CHART_REQUEST,
            QueryIntent.EXPORT_REQUEST,
        ):
            log.info("router.dispatch.document_rag")
            return await self._rag_agent.handle(
                query=query,
                user_id=user_id,
                session_id=session_id,
                intent=intent,
                db=db,
            )

        # Default: general Q&A
        log.info("router.dispatch.general_llm")
        return await self._general_agent.handle(
            query=query,
            user_id=user_id,
            session_id=session_id,
        )
