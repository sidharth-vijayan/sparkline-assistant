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

# The device actually in use, which is not always the configured one: the GPU is
# shared with Ollama, and this service drops to CPU rather than fail an upload.
_device: str | None = None


def get_active_device() -> str | None:
    """Device the model is currently on, or None before it has been loaded."""
    return _device


def _get_model() -> SentenceTransformer:
    global _model, _device
    if _model is None:
        logger.info(
            "embedding_service.loading",
            model=settings.embedding_model_name,
            device=settings.embedding_device,
        )
        try:
            _model = SentenceTransformer(
                settings.embedding_model_name,
                device=settings.embedding_device,
            )
            _device = settings.embedding_device
        except Exception as e:
            if settings.embedding_device == "cpu" or "out of memory" not in str(e).lower():
                raise
            # No room on the card at startup — come up on CPU rather than
            # leaving ingestion broken until someone notices.
            logger.warning("embedding_service.load_oom_falling_back_to_cpu", error=str(e))
            _model = SentenceTransformer(settings.embedding_model_name, device="cpu")
            _device = "cpu"
        logger.info("embedding_service.ready", device=_device)
    return _model


def _fall_back_to_cpu() -> SentenceTransformer:
    """
    Move the model to CPU permanently for this process, and return it.

    Called when the GPU runs out of memory. The card is shared with the LLM
    served by Ollama, whose footprint changes as models are loaded and evicted,
    so free VRAM is not something this service can rely on or reserve. Slower
    embedding is a far better outcome than a failed upload, and doing it once
    per process avoids thrashing between devices on every batch.
    """
    global _model, _device

    logger.warning("embedding_service.cuda_oom_falling_back_to_cpu")
    try:
        if _model is not None:
            _model.to("cpu")
        import torch

        torch.cuda.empty_cache()
    except Exception as e:  # pragma: no cover - best effort cleanup
        logger.warning("embedding_service.cpu_fallback_cleanup_failed", error=str(e))

    _device = "cpu"
    return _model  # type: ignore[return-value]


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """
    Embed a list of texts and return a list of float vectors.

    Args:
        texts: List of strings to embed
        batch_size: Number of texts per inference batch. Measured on the
            RTX 5060 Ti, throughput is flat from 32 to 128 — this is
            compute-bound, not batch-bound — while larger batches are the
            first thing to run out of memory when the LLM is resident. There
            is nothing to gain by raising it and something to lose.

    Returns:
        List of vectors, one per input text (length = QDRANT_VECTOR_SIZE)
    """
    if not texts:
        return []

    model = _get_model()

    def _encode(m: SentenceTransformer) -> list[list[float]]:
        # normalize_embeddings=True is required for cosine similarity scoring
        return m.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    try:
        return _encode(model)
    except Exception as e:
        # torch.OutOfMemoryError is a subclass of RuntimeError; match on the
        # message so this keeps working across torch versions.
        if "out of memory" not in str(e).lower():
            raise
        return _encode(_fall_back_to_cpu())


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string.

    BGE models benefit from a query prefix for retrieval tasks.
    """
    prefixed = f"Represent this sentence for searching relevant passages: {query}"
    result = embed_texts([prefixed])
    return result[0]
