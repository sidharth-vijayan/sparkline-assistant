"""
retrieval/reranker.py
──────────────────────
Cross-encoder reranker on top of hybrid retrieval results.

Uses sentence-transformers CrossEncoder (ms-marco-MiniLM-L-6-v2 by default).
Device is config-driven (cpu / cuda) — set RERANKER_DEVICE=cuda in .env
once the RTX 5060 Ti is available. No code changes required.

Reranking is the final ranking step before the top-k chunks are passed
to the LLM prompt. It significantly improves precision over pure vector
similarity by directly scoring query-chunk relevance pairs.
"""

from __future__ import annotations

import structlog
from sentence_transformers import CrossEncoder

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        logger.info(
            "reranker.loading",
            model=settings.reranker_model_name,
            device=settings.reranker_device,
        )
        _reranker = CrossEncoder(
            settings.reranker_model_name,
            device=settings.reranker_device,
            max_length=512,
        )
        logger.info("reranker.ready")
    return _reranker


def rerank(
    query: str,
    candidates: list[dict],
    top_k: int | None = None,
) -> list[dict]:
    """
    Rerank a list of chunk candidates using the cross-encoder.

    Args:
        query: Original user query
        candidates: List of chunk payload dicts (must have 'text' key)
        top_k: How many top results to return (default: settings.retrieval_top_k_rerank)

    Returns:
        Reranked list of chunk dicts with 'rerank_score' added, top_k only.
    """
    if not candidates:
        return []

    k = top_k or settings.retrieval_top_k_rerank
    reranker = _get_reranker()

    query_chunk_pairs = [(query, c["text"]) for c in candidates]
    scores = reranker.predict(query_chunk_pairs, show_progress_bar=False)

    # Attach rerank scores and sort descending
    scored = sorted(
        zip(candidates, scores),
        key=lambda x: x[1],
        reverse=True,
    )

    results = []
    for chunk, score in scored[:k]:
        enriched = dict(chunk)
        enriched["rerank_score"] = float(score)
        results.append(enriched)

    logger.debug(
        "reranker.complete",
        input_candidates=len(candidates),
        output_k=len(results),
        top_score=round(results[0]["rerank_score"], 4) if results else None,
    )
    return results
