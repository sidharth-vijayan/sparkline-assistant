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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SessionDocument:
    """One uploaded attachment, as an administrator needs to see it.

    Answers "who attached what, where, and when" without anyone having to open
    Qdrant by hand — the unit an admin lists and withdraws.
    """

    session_document_id: str
    chat_id: str
    owner_user_id: str
    document_name: str
    uploaded_at: datetime
    chunk_count: int
    # The Open WebUI file this came from, when the pipe uploaded it. None for a
    # direct API upload. Lets the pipe tell which of a chat's files we already
    # hold, since Open WebUI re-sends the whole list on every message.
    source_file_id: str | None = None


class SessionDocumentStore:
    """Per-chat attachment chunks in a dedicated Qdrant collection."""

    def __init__(
        self,
        collection_name: Optional[str] = None,
        client: Optional[QdrantClient] = None,
    ) -> None:
        resolved = collection_name or settings.qdrant_session_collection_name
        # This class drops collections and deletes by filter, and its tests run
        # against the production Qdrant isolated only by name. Refusing the
        # corpus collection here means that isolation does not depend on
        # whoever writes the next test choosing the right string.
        if resolved == settings.qdrant_collection_name:
            raise ValueError(
                f"refusing to operate on the document corpus collection "
                f"'{resolved}' — SessionDocumentStore deletes collections and "
                f"points, and must never be pointed at the corpus"
            )
        self.collection_name = resolved
        self._client = client or QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key or None,
            # Required whenever api_key is set: qdrant-client switches to TLS on
            # its own when it sees a key, and this Qdrant serves plaintext. See
            # the longer note in services/qdrant_service.py.
            https=False,
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
        source_file_id: Optional[str] = None,
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
                "source_file_id": source_file_id,
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

    def list_documents(
        self,
        owner_user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> list[SessionDocument]:
        """
        Enumerate stored attachments, newest first.

        Attachments never expire, so this is the only way anyone finds out what
        the store is holding. Both filters are optional: no arguments lists
        everything, which is the administrator's view.
        """
        conditions = []
        if owner_user_id:
            conditions.append(
                qmodels.FieldCondition(
                    key="owner_user_id", match=qmodels.MatchValue(value=owner_user_id)
                )
            )
        if chat_id:
            conditions.append(
                qmodels.FieldCondition(
                    key="chat_id", match=qmodels.MatchValue(value=chat_id)
                )
            )
        scroll_filter = qmodels.Filter(must=conditions) if conditions else None

        # Chunks are the stored unit; a document is an aggregate over them.
        docs: dict[str, dict] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=scroll_filter,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                doc_id = payload.get("session_document_id")
                if not doc_id:
                    continue
                entry = docs.setdefault(
                    doc_id,
                    {
                        "chat_id": payload.get("chat_id", ""),
                        "owner_user_id": payload.get("owner_user_id", ""),
                        "document_name": payload.get("document_name", ""),
                        "uploaded_at_ts": float(payload.get("uploaded_at_ts") or 0.0),
                        "source_file_id": payload.get("source_file_id"),
                        "chunk_count": 0,
                    },
                )
                entry["chunk_count"] += 1
            if offset is None:
                break

        return sorted(
            (
                SessionDocument(
                    session_document_id=doc_id,
                    chat_id=e["chat_id"],
                    owner_user_id=e["owner_user_id"],
                    document_name=e["document_name"],
                    uploaded_at=datetime.fromtimestamp(
                        e["uploaded_at_ts"], tz=timezone.utc
                    ),
                    chunk_count=e["chunk_count"],
                    source_file_id=e["source_file_id"],
                )
                for doc_id, e in docs.items()
            ),
            key=lambda d: d.uploaded_at,
            reverse=True,
        )

    def attached_source_file_ids(self, chat_id: str) -> set[str]:
        """
        Which Open WebUI files this chat already holds attachments for.

        Open WebUI hands a pipe the chat's whole file list on every message,
        not just the turn a file was attached, so without this the pipe would
        re-embed the same document on each turn.
        """
        if not chat_id:
            return set()
        return {
            d.source_file_id
            for d in self.list_documents(chat_id=chat_id)
            if d.source_file_id
        }

    def delete_document(self, session_document_id: str) -> int:
        """
        Withdraw a single attachment, leaving the rest of its chat in place.

        The sweep deletes whole chats; this is the administrator's scalpel for
        one file. Returns the number of chunks removed.
        """
        if not session_document_id:
            return 0

        matching = [
            d for d in self.list_documents()
            if d.session_document_id == session_document_id
        ]
        if not matching:
            return 0
        freed = matching[0].chunk_count

        self._client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="session_document_id",
                            match=qmodels.MatchValue(value=session_document_id),
                        )
                    ]
                )
            ),
        )
        logger.info(
            "session_store.document_deleted",
            session_document_id=session_document_id,
            chunks=freed,
        )
        return freed

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
