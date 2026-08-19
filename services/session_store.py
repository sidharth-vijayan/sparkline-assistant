"""
services/session_store.py
──────────────────────────
Storage for per-chat file attachments.

Attachments live in their own Qdrant collection, separate from the document
corpus. That separation — not a payload flag — is what keeps one person's
upload out of everybody else's retrieval: a corpus search cannot return a
session chunk even if its filter is wrong, because it is not searching the
collection those chunks are in.

Attachments have no TTL. They live as long as their chat does, and are
reclaimed by the reconciliation sweep in maintenance/reconciliation.py.
`expires_at` is carried in the payload but left None, so a time-based policy
can be added later without re-ingesting anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from access_control.pep import build_session_filter
from config.settings import get_settings
from maintenance.reconciliation import HeldChat

logger = structlog.get_logger(__name__)
settings = get_settings()

_SCROLL_BATCH = 256


class SessionDocumentStore:
    """Per-chat attachment chunks in a dedicated Qdrant collection."""

    def __init__(
        self,
        collection_name: Optional[str] = None,
        client: Optional[QdrantClient] = None,
    ) -> None:
        self.collection_name = collection_name or settings.qdrant_session_collection_name
        self._client = client or QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
            timeout=30,
        )

    # ── Collection lifecycle ──────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Create the session collection and its payload indexes if absent."""
        try:
            self._client.get_collection(self.collection_name)
            return
        except Exception:
            pass

        self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=qmodels.VectorParams(
                size=settings.qdrant_vector_size,
                distance=qmodels.Distance.COSINE,
            ),
        )
        # Filtered on every session search, so indexed.
        for field in ("chat_id", "owner_user_id", "session_document_id"):
            self._client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
        logger.info("session_store.collection_created", collection=self.collection_name)

    def drop_collection(self) -> None:
        """Delete the whole collection. Used by tests and by a full reset."""
        try:
            self._client.delete_collection(self.collection_name)
        except Exception as e:
            logger.debug("session_store.drop_noop", error=str(e))

    # ── Writing ───────────────────────────────────────────────────────────

    def upsert_chunks(
        self,
        chunks: list[dict],
        chat_id: str,
        owner_user_id: str,
        document_name: str,
        uploaded_at: Optional[datetime] = None,
    ) -> str:
        """
        Store one uploaded document's chunks against a chat.

        Returns the session_document_id, the unit of deletion for a single file
        (the sweep deletes by chat; this allows removing one attachment).
        """
        if not chat_id or not owner_user_id:
            raise ValueError("chat_id and owner_user_id are required")

        uploaded_at = uploaded_at or datetime.now(timezone.utc)
        session_document_id = str(uuid.uuid4())

        points = []
        for chunk in chunks:
            payload = {
                "chunk_id": str(chunk.get("chunk_id", "")),
                "session_document_id": session_document_id,
                "chat_id": chat_id,
                "owner_user_id": owner_user_id,
                "document_name": document_name,
                "text": chunk["text"],
                "page_number": chunk.get("page_number"),
                "chunk_index": chunk.get("chunk_index", 0),
                "uploaded_at": uploaded_at.isoformat(),
                # Epoch copy so the sweep can sort and range-filter without
                # parsing ISO strings out of every payload.
                "uploaded_at_ts": uploaded_at.timestamp(),
                # Reserved for a future time-based policy. None = never expires.
                "expires_at": None,
            }
            points.append(
                qmodels.PointStruct(
                    # Qdrant point IDs must be UUID or int, so the caller's
                    # chunk_id stays in the payload and we mint our own.
                    id=str(uuid.uuid4()),
                    vector=chunk["embedding"],
                    payload=payload,
                )
            )

        if points:
            self._client.upsert(collection_name=self.collection_name, points=points)
        logger.info(
            "session_store.upserted",
            chat_id=chat_id,
            document=document_name,
            chunks=len(points),
        )
        return session_document_id

    # ── Reading ───────────────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        chat_id: str,
        user_id: str,
        top_k: int = 10,
    ) -> list[dict]:
        """Dense search restricted to one user's attachments in one chat."""
        response = self._client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=build_session_filter(chat_id=chat_id, user_id=user_id),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {"qdrant_point_id": str(r.id), "score": r.score, "payload": r.payload}
            for r in response.points
        ]

    def held_chats(self) -> list[HeldChat]:
        """
        Every chat we hold attachments for, with chunk count and newest upload
        time. This is the left-hand side of the reconciliation sweep.
        """
        counts: dict[str, int] = {}
        newest: dict[str, float] = {}

        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection_name,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=["chat_id", "uploaded_at_ts"],
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                chat_id = payload.get("chat_id")
                if not chat_id:
                    continue
                counts[chat_id] = counts.get(chat_id, 0) + 1
                ts = float(payload.get("uploaded_at_ts") or 0.0)
                if ts > newest.get(chat_id, 0.0):
                    newest[chat_id] = ts
            if offset is None:
                break

        return [
            HeldChat(
                chat_id=chat_id,
                newest_upload_at=datetime.fromtimestamp(
                    newest.get(chat_id, 0.0), tz=timezone.utc
                ),
                chunk_count=count,
            )
            for chat_id, count in sorted(counts.items())
        ]

    # ── Deleting ──────────────────────────────────────────────────────────

    def delete_by_chat_ids(self, chat_ids: Iterable[str]) -> int:
        """
        Delete every attachment chunk belonging to the given chats.

        Returns the number of chunks removed. An empty input deletes nothing
        and, importantly, does not fall through to an unfiltered delete.
        """
        ids = [c for c in chat_ids if c]
        if not ids:
            return 0

        held = {h.chat_id: h.chunk_count for h in self.held_chats()}
        freed = sum(held.get(c, 0) for c in ids)

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="chat_id", match=qmodels.MatchAny(any=ids)
                        )
                    ]
                )
            ),
        )
        logger.info("session_store.deleted", chats=len(ids), chunks=freed)
        return freed
