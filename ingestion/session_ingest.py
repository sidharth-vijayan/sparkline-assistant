"""
ingestion/session_ingest.py
────────────────────────────
Turn a file someone attached to a chat into retrievable chunks.

Shares the corpus parsers and chunker — a .docx is read the same way whoever
uploaded it — but diverges after that in three ways that matter:

  - Nothing is written to Postgres. Attachments are not documents; they have no
    versions, no access-control tags, and no audit trail of their own.
  - Nothing enters the BM25 index. build_index() rebuilds the whole corpus, so
    doing it per attachment would be absurdly expensive; attachments are
    dense-retrieval only, which is fine for a single small file.
  - Chunks are scoped to a chat and an owner at write time, not just at read
    time, so an attachment that could never be read back is never written.

Attachments never expire, so the chunk cap here is the only thing bounding
permanent growth. It is deliberately far tighter than the corpus limit: a
person attaching a spreadsheet to a conversation wants to ask about it, not
index it forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import structlog

from ingestion.pipeline import parse_and_chunk

logger = structlog.get_logger(__name__)

# Formats a chat attachment may use. Same readers as the corpus path.
ALLOWED_SESSION_EXTENSIONS = {
    ".pdf",
    ".docx", ".doc",
    ".xlsx", ".xlsm", ".xls",
    ".csv", ".tsv",
    ".txt", ".md", ".log",
}

# Far below INGEST_MAX_CHUNKS_PER_DOCUMENT (2000). An attachment is something
# to ask questions about in one conversation, and it never ages out.
MAX_SESSION_CHUNKS = 200

MAX_SESSION_FILE_BYTES = 25 * 1024 * 1024


class SessionIngestError(Exception):
    """The file could not be turned into anything retrievable."""


class UnsupportedSessionFile(SessionIngestError):
    """The file type has no parser."""


class AttachmentStore(Protocol):
    def upsert_chunks(
        self,
        chunks: list[dict],
        chat_id: str,
        owner_user_id: str,
        document_name: str,
        uploaded_at: Optional[datetime] = None,
        source_file_id: Optional[str] = None,
    ) -> str: ...


@dataclass(frozen=True)
class IngestedAttachment:
    """What was stored, for the upload response and the admin listing."""

    session_document_id: str
    document_name: str
    chat_id: str
    chunk_count: int
    truncated: bool


def ingest_session_document(
    file_bytes: bytes,
    filename: str,
    chat_id: str,
    owner_user_id: str,
    store: Optional[AttachmentStore] = None,
    embed: Optional[Callable[[list[str]], list[list[float]]]] = None,
    source_file_id: Optional[str] = None,
) -> IngestedAttachment:
    """
    Parse, chunk, embed and store one chat attachment.

    Args:
        file_bytes: Raw uploaded bytes.
        filename: Original name, kept for citations and the admin listing.
        chat_id: The Open WebUI chat this belongs to.
        owner_user_id: The Sparkline user uploading it.
        store: Where chunks go. Defaults to the real SessionDocumentStore.
        embed: Text -> vectors. Injected so tests need no embedding model.

    Raises:
        ValueError: chat_id or owner_user_id missing.
        UnsupportedSessionFile: no parser for this extension.
        SessionIngestError: file empty, or yielded no readable text.
    """
    if not chat_id:
        raise ValueError(
            "chat_id is required — an attachment without one could never be "
            "retrieved and could never be swept"
        )
    if not owner_user_id:
        raise ValueError("owner_user_id is required to scope the attachment")

    if not file_bytes:
        raise SessionIngestError(f"'{filename}' is empty")
    if len(file_bytes) > MAX_SESSION_FILE_BYTES:
        raise SessionIngestError(
            f"'{filename}' is larger than the "
            f"{MAX_SESSION_FILE_BYTES // (1024 * 1024)} MB attachment limit"
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SESSION_EXTENSIONS:
        raise UnsupportedSessionFile(
            f"'{suffix or filename}' cannot be attached. Supported: "
            + ", ".join(sorted(ALLOWED_SESSION_EXTENSIONS))
        )

    try:
        chunks = parse_and_chunk(file_bytes, filename, suffix)
    except ValueError as e:
        raise SessionIngestError(str(e)) from e

    if not chunks:
        raise SessionIngestError(f"'{filename}' produced no readable text")

    truncated = len(chunks) > MAX_SESSION_CHUNKS
    if truncated:
        logger.warning(
            "session_ingest.truncated",
            document=filename,
            produced=len(chunks),
            kept=MAX_SESSION_CHUNKS,
        )
        chunks = chunks[:MAX_SESSION_CHUNKS]

    embed_fn = embed or _default_embed
    vectors = embed_fn([c.text for c in chunks])

    payload_chunks = [
        {
            "chunk_id": f"{chat_id}:{c.chunk_index}",
            "text": c.text,
            "embedding": vector,
            "page_number": c.page_number,
            "chunk_index": c.chunk_index,
        }
        for c, vector in zip(chunks, vectors)
    ]

    target = store or _default_store()
    session_document_id = target.upsert_chunks(
        chunks=payload_chunks,
        chat_id=chat_id,
        owner_user_id=owner_user_id,
        document_name=filename,
        uploaded_at=datetime.now(timezone.utc),
        source_file_id=source_file_id,
    )

    logger.info(
        "session_ingest.complete",
        document=filename,
        chat_id=chat_id,
        chunks=len(payload_chunks),
        truncated=truncated,
    )
    return IngestedAttachment(
        session_document_id=session_document_id,
        document_name=filename,
        chat_id=chat_id,
        chunk_count=len(payload_chunks),
        truncated=truncated,
    )


def _default_embed(texts: list[str]) -> list[list[float]]:
    from services.embedding_service import embed_texts

    return embed_texts(texts)


def _default_store():
    from services.session_store import SessionDocumentStore

    store = SessionDocumentStore()
    store.ensure_collection()
    return store
