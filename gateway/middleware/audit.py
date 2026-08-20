"""
gateway/middleware/audit.py
────────────────────────────
Audit logging to the shared PostgreSQL audit_log table.

Both the Document RAG agent and Dhruv's enterprise adapter agents
log here. The agent_type field distinguishes which workstream generated
each entry, making the log queryable across both workstreams.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog

logger = structlog.get_logger(__name__)


async def log_query(
    db: AsyncSession,
    user_id: Optional[str],
    query_text: str,
    agent_type: str,
    session_id: Optional[str] = None,
    pdp_decision: Optional[str] = None,
    retrieved_doc_version_ids: Optional[list[str]] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """
    Write an audit log entry.

    Args:
        db: Async database session
        user_id: UUID string of the querying user (None for unauthenticated)
        query_text: The user's query
        agent_type: 'general' | 'document_rag' | 'enterprise_crm' | etc.
        session_id: Redis session ID
        pdp_decision: 'allow' | 'deny' | None
        retrieved_doc_version_ids: List of document version UUIDs that were retrieved
        latency_ms: Total request latency in milliseconds

    The answer itself is not recorded. See the note on AuditLog in db/models.py:
    storing a slice of the response duplicated document text into a table with
    no access control over it, and nothing read it back.
    """
    try:
        entry = AuditLog(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id) if user_id else None,
            agent_type=agent_type,
            query_text=query_text[:10000],  # Truncate very long queries
            session_id=session_id,
            pdp_decision=pdp_decision,
            retrieved_doc_version_ids=retrieved_doc_version_ids or [],
            latency_ms=latency_ms,
            created_at=datetime.now(timezone.utc),
        )
        db.add(entry)
        # Don't commit here — caller commits the outer transaction

        logger.debug(
            "audit.logged",
            user_id=user_id,
            agent_type=agent_type,
            pdp_decision=pdp_decision,
            latency_ms=latency_ms,
        )
    except Exception as e:
        # Audit failure must never crash the main request
        logger.error("audit.log_failed", error=str(e), user_id=user_id)


class AuditTimer:
    """Context manager to time a request and return elapsed ms."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed_ms: int = 0

    def __enter__(self) -> "AuditTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
