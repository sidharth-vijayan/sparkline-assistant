"""
ingestion/embedder.py
──────────────────────
Embed a list of TextChunks and upsert them into Qdrant.

Called by the ingestion pipeline after parse + chunk + Postgres store.
Separated from pipeline.py so it can be tested independently and
so the embedding step can be run asynchronously if needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

from ingestion.chunker import TextChunk
from services.embedding_service import embed_texts
from services.qdrant_service import deactivate_version, ensure_collection_exists, upsert_chunks

logger = structlog.get_logger(__name__)


def embed_and_index_chunks(
    chunks: list[TextChunk],
    chunk_qdrant_ids: list[uuid.UUID],   # Must align 1:1 with chunks
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    document_name: str,
    uploaded_at: datetime,
    allowed_departments: list[str] | None,
    allowed_designations: list[str] | None,
    is_public: bool,
    previous_version_id: uuid.UUID | None = None,
    batch_size: int = 64,
) -> None:
    """
    Embed all chunks and upsert them into Qdrant.

    Args:
        chunks: TextChunk list from the chunker
        chunk_qdrant_ids: Qdrant point UUIDs assigned by the pipeline (1:1 with chunks)
        document_id: Parent document UUID
        document_version_id: This version's UUID
        document_name: Human-readable filename for citation
        uploaded_at: Upload timestamp for citation
        allowed_departments: Access control — set by file-admin
        allowed_designations: Access control — set by file-admin
        is_public: If True, no dept/desig filtering
        previous_version_id: UUID of the version being replaced (will be deactivated)
        batch_size: Embedding batch size (reduce if OOM on CPU)
    """
    ensure_collection_exists()

    # Deactivate previous version's Qdrant chunks (soft-delete)
    if previous_version_id:
        deactivate_version(previous_version_id)
        logger.info("embedder.previous_version_deactivated", version_id=str(previous_version_id))

    # Batch-embed all chunk texts
    texts = [chunk.text for chunk in chunks]
    logger.info("embedder.embedding_start", chunk_count=len(texts))

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_embeddings = embed_texts(batch)
        all_embeddings.extend(batch_embeddings)
        logger.debug("embedder.batch_done", batch=i // batch_size + 1, size=len(batch))

    logger.info("embedder.embedding_complete", total=len(all_embeddings))

    # Build payload dicts for Qdrant upsert
    chunk_dicts = []
    for chunk, embedding, qdrant_id in zip(chunks, all_embeddings, chunk_qdrant_ids):
        chunk_dicts.append(
            {
                "chunk_id": str(qdrant_id),
                "qdrant_point_id": qdrant_id,
                "text": chunk.text,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "embedding": embedding,
            }
        )

    # Upsert to Qdrant in batches of 256 (Qdrant default max batch)
    QDRANT_BATCH = 256
    for i in range(0, len(chunk_dicts), QDRANT_BATCH):
        batch = chunk_dicts[i : i + QDRANT_BATCH]
        upsert_chunks(
            chunks=batch,
            document_id=document_id,
            document_version_id=document_version_id,
            document_name=document_name,
            uploaded_at=uploaded_at.isoformat(),
            allowed_departments=allowed_departments,
            allowed_designations=allowed_designations,
            is_public=is_public,
        )

    logger.info(
        "embedder.index_complete",
        document_name=document_name,
        version_id=str(document_version_id),
        total_chunks=len(chunk_dicts),
    )
