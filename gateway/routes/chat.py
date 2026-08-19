"""
gateway/routes/chat.py
───────────────────────
/v1/chat/completions endpoint — OpenAI-compatible API.

Open WebUI (and the Pipeline) talks to this endpoint.
The route authenticates the user, creates/resumes a session,
routes the query through the two-step router, and returns
an OpenAI-compatible response with citations.
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User
from gateway.middleware.audit import AuditTimer, log_query
from gateway.middleware.auth import get_current_user
from router.query_router import QueryRouter
from services.postgres_service import get_db
from services.redis_service import create_session, get_session_meta

logger = structlog.get_logger(__name__)
router = APIRouter()
_query_router = QueryRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = None   # Accepted but ignored — model is config-driven
    messages: list[ChatMessage]
    session_id: Optional[str] = None
    # Open WebUI's chat ID. Scopes per-chat attachments; absent for API callers
    # that are not a chat, in which case no attachment is ever in scope.
    chat_id: Optional[str] = None
    stream: Optional[bool] = False


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    model: str
    choices: list[dict]
    citations: list[dict] = []
    tool_outputs: list[dict] = []
    agent_type: str
    intent: str


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Main chat endpoint. Accepts OpenAI-compatible message format.

    The 'messages' list is used to extract the user's latest query.
    Full conversation history is managed server-side in Redis.
    """
    from config.settings import get_settings
    settings = get_settings()

    # Extract the latest user message
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found in request",
        )
    query = user_messages[-1].content

    # Session management
    session_id = request.session_id or str(uuid.uuid4())
    session_meta = await get_session_meta(session_id)
    if session_meta is None:
        await create_session(session_id=session_id, user_id=str(current_user.id))

    log = logger.bind(
        user_id=str(current_user.id),
        session_id=session_id,
        query_len=len(query),
    )
    log.info("chat.request")

    # Route and execute
    with AuditTimer() as timer:
        agent_response = await _query_router.route(
            query=query,
            user_id=str(current_user.id),
            session_id=session_id,
            db=db,
            chat_id=request.chat_id,
        )

    # Write audit log (non-blocking — failure won't crash the request)
    retrieved_ids = [
        c.get("version_uploaded_at", "")
        for c in agent_response.get("citations", [])
    ]
    await log_query(
        db=db,
        user_id=str(current_user.id),
        query_text=query,
        agent_type=agent_response.get("agent_type", "unknown"),
        session_id=session_id,
        pdp_decision=agent_response.get("pdp_decision"),
        retrieved_doc_version_ids=retrieved_ids,
        response_summary=agent_response.get("answer", "")[:500],
        latency_ms=timer.elapsed_ms,
    )

    answer = agent_response.get("answer", "")
    citations = agent_response.get("citations", [])

    # Format as OpenAI-compatible response
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "model": settings.llm_model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                },
                "finish_reason": "stop",
            }
        ],
        "citations": citations,
        "tool_outputs": agent_response.get("tool_outputs", []),
        "agent_type": agent_response.get("agent_type", "unknown"),
        "intent": agent_response.get("intent", "unknown"),
        # Top cross-encoder rerank score behind the routing decision. Exposed so
        # the score bands can be retuned against real pilot traffic rather than
        # only against the calibration set. None when the query never reached
        # retrieval (small talk) or was answered from general knowledge.
        "top_rerank_score": agent_response.get("top_rerank_score"),
        "session_id": session_id,
    }
