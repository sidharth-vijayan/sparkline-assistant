"""
retrieval/hybrid_retrieval.py
──────────────────────────────
Hybrid retrieval: BM25 + dense vector search, merged via Reciprocal Rank Fusion (RRF).

RRF formula:  score(d) = Σ  1 / (k + rank(d))
where k is a constant (default 60) that controls rank decay steepness.

The PEP (access control) filter is applied to the Qdrant dense search only.
BM25 candidates are resolved against Qdrant payloads (via point IDs) so that
only chunks the user is permitted to see are surfaced. This means:
  - Qdrant handles access enforcement at retrieval time
  - BM25 provides keyword recall, but its results are cross-referenced
    with the Qdrant-permitted set before being ranked

Returns a merged, RRF-ranked list of chunk payloads ready for reranking.
"""

from __future__ import annotations

import structlog
from qdrant_client.http import models as qmodels

from config.settings import get_settings
from ingestion.bm25_index import search as bm25_search
from services.embedding_service import embed_query
from services.qdrant_service import search_dense

logger = structlog.get_logger(__name__)
settings = get_settings()


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int | None = None,
) -> list[tuple[str, float]]:
    """
    Merge multiple ranked lists using RRF.

    Args:
        ranked_lists: Each inner list is an ordered list of item IDs (best first)
        k: RRF constant (default: settings.retrieval_rrf_k)

    Returns:
        List of (item_id, rrf_score) sorted descending by score
    """
    rrf_k = k or settings.retrieval_rrf_k
    scores: dict[str, float] = {}

    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (rrf_k + rank)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def hybrid_search(
    query: str,
    qdrant_filter: qmodels.Filter | None,
    top_k_dense: int | None = None,
    top_k_bm25: int | None = None,
    top_k_final: int | None = None,
) -> list[dict]:
    """
    Run hybrid retrieval: BM25 + dense, merged via RRF.

    Args:
        query: User query string
        qdrant_filter: Access-control filter from the PEP (applied to dense search)
        top_k_dense: Dense retrieval candidates per search
        top_k_bm25: BM25 candidates per search
        top_k_final: Candidates to keep after the RRF merge. This is the pool the
            cross-encoder reranker then scores and cuts down to the final k — it
            is deliberately NOT the final answer size. Defaults to
            settings.retrieval_top_k_fusion.

    Returns:
        List of chunk payload dicts, RRF-ranked, limited to top_k_final.
        Each dict has the full Qdrant payload plus 'rrf_score'.
    """
    k_dense = top_k_dense or settings.retrieval_top_k_dense
    k_bm25 = top_k_bm25 or settings.retrieval_top_k_bm25
    k_final = top_k_final or settings.retrieval_top_k_fusion

    # ── Dense retrieval ───────────────────────────────────────────
    query_vector = embed_query(query)
    dense_results = search_dense(
        query_vector=query_vector,
        qdrant_filter=qdrant_filter,
        top_k=k_dense,
    )
    # Map: qdrant_point_id → payload (for BM25 cross-reference below)
    dense_payloads: dict[str, dict] = {
        r["qdrant_point_id"]: r["payload"] for r in dense_results
    }
    dense_ranked = [r["qdrant_point_id"] for r in dense_results]

    logger.debug("hybrid.dense_retrieved", count=len(dense_results))

    # ── BM25 retrieval ────────────────────────────────────────────
    bm25_results = bm25_search(query, top_k=k_bm25)
    # Filter BM25 results to only those in the Qdrant-permitted set
    # (dense_payloads acts as the access-controlled allow-list)
    bm25_permitted = [r for r in bm25_results if r["qdrant_point_id"] in dense_payloads]
    bm25_ranked = [r["qdrant_point_id"] for r in bm25_permitted]

    logger.debug(
        "hybrid.bm25_retrieved",
        total=len(bm25_results),
        permitted=len(bm25_permitted),
    )

    # ── RRF Merge ────────────────────────────────────────────────
    all_candidate_ids = list(
        set(dense_ranked) | set(bm25_ranked)
    )

    rrf_scores = dict(
        reciprocal_rank_fusion([dense_ranked, bm25_ranked])
    )

    # Resolve payloads: prefer dense_payloads; BM25-only hits won't have payloads
    # (they're already filtered to dense_payloads above, so all have payloads)
    merged = []
    for point_id, rrf_score in sorted(
        rrf_scores.items(), key=lambda x: x[1], reverse=True
    )[:k_final]:
        if point_id in dense_payloads:
            result = dict(dense_payloads[point_id])
            result["rrf_score"] = rrf_score
            result["qdrant_point_id"] = point_id
            merged.append(result)

    logger.info(
        "hybrid.merge_complete",
        dense_candidates=len(dense_ranked),
        bm25_candidates=len(bm25_ranked),
        merged=len(merged),
    )
    return merged
