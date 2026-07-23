"""
services/qdrant_service.py
───────────────────────────
Qdrant vector database client wrapper.

Manages:
  - Collection creation with correct vector config
  - Upsert of chunk embeddings with full metadata payload
  - Dense vector search with metadata filtering
  - Soft-delete (filtering by active version_id) for versioning

The collection stores dense vectors (BAAI/bge-large-en, cosine similarity).
BM25 keyword search is handled separately in ingestion/bm25_index.py.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_client: QdrantClient | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {
            "host": settings.qdrant_host,
            "port": settings.qdrant_port,
            "grpc_port": settings.qdrant_grpc_port,
            "prefer_grpc": True,
        }
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        _client = QdrantClient(**kwargs)
        logger.info("qdrant.client_ready", host=settings.qdrant_host)
    return _client


def ensure_collection_exists() -> None:
    """Create the Sparkline documents collection if it doesn't exist."""
    client = _get_client()
    collection_name = settings.qdrant_collection_name

    try:
        client.get_collection(collection_name)
        logger.info("qdrant.collection_exists", collection=collection_name)
    except (UnexpectedResponse, Exception):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=settings.qdrant_vector_size,
                distance=qmodels.Distance.COSINE,
            ),
        )
        # Create payload indexes for efficient metadata filtering
        client.create_payload_index(
            collection_name=collection_name,
            field_name="document_version_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="document_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="is_active_version",
            field_schema=qmodels.PayloadSchemaType.BOOL,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="allowed_departments",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="allowed_designations",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="is_public",
            field_schema=qmodels.PayloadSchemaType.BOOL,
        )
        logger.info("qdrant.collection_created", collection=collection_name)


def upsert_chunks(
    chunks: list[dict],
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    document_name: str,
    uploaded_at: str,
    allowed_departments: Optional[list[str]],
    allowed_designations: Optional[list[str]],
    is_public: bool,
) -> None:
    """
    Upsert a batch of chunk vectors into Qdrant.

    Each chunk dict must have:
      - qdrant_point_id: UUID
      - embedding: list[float]
      - text: str
      - page_number: int | None
      - chunk_index: int
    """
    client = _get_client()
    collection_name = settings.qdrant_collection_name

    points = []
    for chunk in chunks:
        payload = {
            # Identity
            "chunk_id": str(chunk["chunk_id"]),
            "document_id": str(document_id),
            "document_version_id": str(document_version_id),
            "document_name": document_name,
            "uploaded_at": uploaded_at,
            # Content
            "text": chunk["text"],
            "page_number": chunk.get("page_number"),
            "chunk_index": chunk["chunk_index"],
            # Versioning — PEP uses this to filter out stale versions
            "is_active_version": True,
            # Access control metadata — set by file-admin at ingestion
            "allowed_departments": allowed_departments or [],
            "allowed_designations": allowed_designations or [],
            "is_public": is_public,
        }
        points.append(
            qmodels.PointStruct(
                id=str(chunk["qdrant_point_id"]),
                vector=chunk["embedding"],
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    logger.info(
        "qdrant.upsert_complete",
        collection=collection_name,
        points=len(points),
        document_id=str(document_id),
        version_id=str(document_version_id),
    )


def deactivate_version(document_version_id: uuid.UUID) -> None:
    """
    Mark all chunks from an old document version as inactive in Qdrant.

    This is a soft-delete: the vectors remain in Qdrant but
    is_active_version=False means the PEP will filter them out.
    Chunks are NOT physically deleted (preserves audit trail).
    """
    client = _get_client()
    collection_name = settings.qdrant_collection_name

    client.set_payload(
        collection_name=collection_name,
        payload={"is_active_version": False},
        points=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="document_version_id",
                    match=qmodels.MatchValue(value=str(document_version_id)),
                )
            ]
        ),
    )
    logger.info(
        "qdrant.version_deactivated",
        version_id=str(document_version_id),
    )


def search_dense(
    query_vector: list[float],
    qdrant_filter: Optional[qmodels.Filter],
    top_k: int = 20,
) -> list[dict]:
    """
    Dense vector search against the active document collection.

    Args:
        query_vector: Embedded query vector
        qdrant_filter: Pre-built filter from the PEP (access control)
        top_k: Number of results to return

    Returns:
        List of result dicts with score + payload
    """
    client = _get_client()
    collection_name = settings.qdrant_collection_name

    results = client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        query_filter=qdrant_filter,
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )

    return [
        {
            "qdrant_point_id": str(r.id),
            "score": r.score,
            "payload": r.payload,
        }
        for r in results
    ]
