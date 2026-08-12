"""
services/redis_service.py
──────────────────────────
Redis session state helpers for the Document RAG agent.

NOTE: This Redis instance is NOT shared with Dhruv's enterprise adapters.
Session state is isolated to the Document RAG workstream per the confirmed
decision (Week 1 kickoff). If cross-agent session continuity is needed
in the future, the Redis URL is config-driven and this can be wired up
without code changes — only a shared config value would need to be set.

Stores per-session:
  - Conversation history (list of messages)
  - Session metadata (user_id, created_at)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def _get_client() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=_get_pool())


# ── Session keys ────────────────────────────────────────────────────────────
def _history_key(session_id: str) -> str:
    return f"session:{session_id}:history"


def _meta_key(session_id: str) -> str:
    return f"session:{session_id}:meta"


# ── Session operations ──────────────────────────────────────────────────────
async def create_session(session_id: str, user_id: str) -> None:
    """Initialize a new session with user metadata."""
    client = _get_client()
    meta = {
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await client.setex(
        _meta_key(session_id),
        settings.redis_session_ttl_seconds,
        json.dumps(meta),
    )
    # Initialize empty history
    await client.delete(_history_key(session_id))
    await client.expire(_history_key(session_id), settings.redis_session_ttl_seconds)
    logger.info("redis.session.created", session_id=session_id, user_id=user_id)


async def append_message(session_id: str, role: str, content: str) -> None:
    """Append a message to the session's conversation history."""
    client = _get_client()
    message = json.dumps({"role": role, "content": content})
    await client.rpush(_history_key(session_id), message)
    await client.expire(_history_key(session_id), settings.redis_session_ttl_seconds)


async def get_history(session_id: str, max_turns: int = 10) -> list[dict[str, str]]:
    """
    Return the most recent conversation turns.

    Args:
        session_id: Session identifier
        max_turns: Maximum number of messages to return (keeps conversation
                   within the LLM context window)
    """
    client = _get_client()
    raw_messages = await client.lrange(_history_key(session_id), -max_turns * 2, -1)
    return [json.loads(m) for m in raw_messages]


async def clear_session(session_id: str) -> None:
    """Clear conversation history for a session (keeps metadata)."""
    client = _get_client()
    await client.delete(_history_key(session_id))
    logger.info("redis.session.cleared", session_id=session_id)


def _last_document_query_key(session_id: str) -> str:
    return f"session:{session_id}:last_doc_query"


# How much of the previous answer to keep in the anchor. Enough to carry the
# entities the follow-up refers to, short enough not to swamp the embedding of
# the question itself.
_ANCHOR_ANSWER_CHARS = 240


async def set_last_document_query(session_id: str, query: str, answer: str = "") -> None:
    """
    Remember the most recent exchange that was answered from documents.

    Follow-ups are expanded with this rather than with the previous turn, because
    users interleave general questions into document conversations ("...and why
    those?" after a detour through "what is 2+2"). Anchoring on the previous turn
    picks up the detour and loses the thread.

    The answer is stored alongside the question because follow-ups routinely
    refer to things only the answer named: after "which agents sit behind the
    orchestrator", "and why were those four chosen?" points at four systems the
    question never mentioned. Anchoring on the question alone retrieves the wrong
    passages and the model refuses.
    """
    client = _get_client()
    anchor = query
    if answer:
        anchor = f"{query} {' '.join(answer.split())[:_ANCHOR_ANSWER_CHARS]}"
    await client.setex(
        _last_document_query_key(session_id),
        settings.redis_session_ttl_seconds,
        anchor,
    )


async def get_last_document_query(session_id: str) -> Optional[str]:
    """Return the last document-answered question in this session, if any."""
    client = _get_client()
    raw = await client.get(_last_document_query_key(session_id))
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


async def get_session_meta(session_id: str) -> Optional[dict[str, Any]]:
    """Return session metadata or None if the session doesn't exist / has expired."""
    client = _get_client()
    raw = await client.get(_meta_key(session_id))
    return json.loads(raw) if raw else None
