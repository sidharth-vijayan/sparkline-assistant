"""
agents/general_llm_agent.py
─────────────────────────────
General LLM agent — passthrough to the LLM without document retrieval.

Handles GENERAL_QUERY intent: questions that don't require document context
(e.g., "What is the formula for ROI?", "Explain what a BOQ is").

Maintains conversation history via Redis for multi-turn coherence.
"""

from __future__ import annotations

import structlog

from services.llm_client import chat_completion, extract_text_response
from services.redis_service import append_message, get_history

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are Sparkline AI, an in-house enterprise assistant for Sparkline,
a construction and equipment company. You help employees with questions about construction,
equipment, HR, finance, sales, and general business topics.

Be concise, professional, and helpful. If you don't know something, say so clearly.
Do not make up facts or fabricate figures."""


class GeneralLLMAgent:
    """Passthrough agent — sends the query directly to the LLM with conversation history."""

    async def handle(
        self,
        query: str,
        user_id: str,
        session_id: str,
    ) -> dict:
        """
        Handle a general (non-RAG) query.

        Returns:
            {
                "answer": str,
                "citations": [],
                "agent_type": "general",
                "intent": "general_query"
            }
        """
        # Load conversation history
        history = await get_history(session_id, max_turns=10)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})

        # Call LLM
        completion = await chat_completion(messages=messages)
        answer = extract_text_response(completion)

        # Store messages in session history
        await append_message(session_id, "user", query)
        await append_message(session_id, "assistant", answer)

        logger.info(
            "general_agent.handled",
            user_id=user_id,
            response_length=len(answer),
        )

        return {
            "answer": answer,
            "citations": [],
            "agent_type": "general",
            "intent": "general_query",
        }
