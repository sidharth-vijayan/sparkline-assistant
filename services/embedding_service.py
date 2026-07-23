"""
services/embedding_service.py
───────────────────────────────
BAAI/bge-large-en embedding service using sentence-transformers.

Device is config-driven (cpu / cuda) — set EMBEDDING_DEVICE=cuda
in .env once the RTX 5060 Ti is available. No code changes required.

The service is a lazy singleton: the model is loaded on first call,
not at import time, so tests can import without triggering model loading.
"""

from __future__ import annotations

import structlog
from sentence_transformers import SentenceTransformer

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info(
            "embedding_service.loading",
            model=settings.embedding_model_name,
            device=settings.embedding_device,
        )
        _model = SentenceTransformer(
            settings.embedding_model_name,
            device=settings.embedding_device,
        )
        logger.info("embedding_service.ready")
    return _model


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """
    Embed a list of texts and return a list of float vectors.

    Args:
        texts: List of strings to embed
        batch_size: Number of texts per inference batch

    Returns:
        List of vectors, one per input text (length = QDRANT_VECTOR_SIZE)
    """
    if not texts:
        return []

    model = _get_model()
    # normalize_embeddings=True is required for cosine similarity scoring
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 100,
    )
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string.

    BGE models benefit from a query prefix for retrieval tasks.
    """
    prefixed = f"Represent this sentence for searching relevant passages: {query}"
    result = embed_texts([prefixed])
    return result[0]
