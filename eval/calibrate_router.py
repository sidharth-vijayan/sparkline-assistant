"""
eval/calibrate_router.py
─────────────────────────
Measure cross-encoder rerank scores for in-corpus vs. out-of-corpus questions,
so the router's ROUTER_RAG_SCORE_HIGH / ROUTER_RAG_SCORE_LOW thresholds are set
from real data rather than guessed.

The reranker (cross-encoder/ms-marco-MiniLM-L-6-v2) emits raw logits, roughly
-11 .. +11 — not probabilities. There is no universal cut-off; the split point
depends on this corpus, so it has to be measured here and re-measured whenever
the corpus changes substantially.

Run inside the api container:
    docker compose -f docker-compose.yml -f docker-compose.server.yml \
        exec api python -m eval.calibrate_router

NOTE: the IN_CORPUS questions below must be answerable from the documents that
are actually ingested. eval/golden_set.json is NOT usable for this — it asks
about safety policy, leave policy, Q3 financials and BOQs, none of which have
ever been ingested. Re-write IN_CORPUS whenever the document set changes.
"""

from __future__ import annotations

import asyncio
import re
import statistics

from access_control.pdp import PDPDecision, PDPResult
from access_control.pep import build_qdrant_filter
from config.settings import get_settings
from retrieval.hybrid_retrieval import hybrid_search
from retrieval.query_normalizer import correct_typos
from retrieval.reranker import rerank

settings = get_settings()

# Answerable from the currently ingested corpus:
#   - "project work split.docx"           (Architecture Guide — layers, ownership, capabilities)
#   - "Sidharth_AI_Assistant_Design.docx" (orchestrator/agents/backend design + reasoning)
IN_CORPUS: list[str] = [
    "Which agents sit behind the orchestrator?",
    "What is used for vector search in the shared backend?",
    "Who owns the document Q&A layer?",
    "What does the memory manager track instead of raw message history?",
    "Which frontend is used as the chat client?",
    "How does routing work in the orchestration layer?",
    "What are the layers a question flows through?",
    "What is stored in MinIO?",
    "Where do closed and summarized tasks move to?",
    "Which MCP tools does the enterprise agent pick between?",
    "What is the responsibility split for RBAC?",
    "Which capabilities are core pilot priorities?",
]

# Not answerable from any ingested document — these must route to the general LLM.
GENERAL: list[str] = [
    "what is 2 + 2",
    "hi",
    "who are you",
    "write a python function to reverse a string",
    "what is the capital of France",
    "explain what depreciation means",
    "translate good morning to Hindi",
    "how do I boil an egg",
    "what's the difference between TCP and UDP",
    "give me tips for a job interview",
    "thanks!",
    "tell me a joke",
    "how many days are in a leap year",
    "what is machine learning",
    "recommend a good book to read",
]


def _introduce_typo(question: str) -> str:
    """
    Mangle the longest word in a question by transposing two of its letters.

    Derived rather than hand-written so this group maintains itself: rewrite
    IN_CORPUS for a new document set and the typo'd variants follow. A
    hand-listed set of misspellings would quietly go stale the moment the
    documents changed — which is the exact failure this group exists to catch.

    Transposition is the most common real typing slip, and it is the one plain
    edit distance scores as two edits rather than one.
    """
    words = re.findall(r"[A-Za-z]{6,}", question)
    if not words:
        return question

    target = max(words, key=len)
    mid = len(target) // 2
    typod = target[:mid - 1] + target[mid] + target[mid - 1] + target[mid + 1:]
    return question.replace(target, typod, 1)


# Same questions as IN_CORPUS, misspelled. These must still route to the
# documents: a misspelled question about a document the system holds, answered
# from general knowledge instead, reads to a user as "it doesn't know its own
# documents" — which is the failure the routing work was done to fix.
IN_CORPUS_TYPOD: list[str] = [_introduce_typo(q) for q in IN_CORPUS]


def _pilot_filter():
    """The Qdrant filter a pilot user gets today (full corpus, active versions)."""
    return build_qdrant_filter(
        PDPResult(
            decision=PDPDecision.ALLOW,
            reason="calibration harness — pilot user stand-in",
            full_access=True,
        )
    )


def top_score(query: str, qdrant_filter) -> tuple[float | None, str, str]:
    """
    Return (top rerank score, top document name, query actually searched with).

    Typo correction is applied here for the same reason the RAG agent applies
    it: these numbers are used to set the routing thresholds, so they have to
    come from the path a real question travels. Measuring the raw query would
    calibrate the router against a pipeline that is not the one running.
    """
    normalized = correct_typos(query)
    search_text = normalized.text

    chunks = hybrid_search(query=search_text, qdrant_filter=qdrant_filter)
    if not chunks:
        return None, "", search_text
    reranked = rerank(query=search_text, candidates=chunks)
    if not reranked:
        return None, "", search_text
    best = reranked[0]
    return best["rerank_score"], best.get("document_name", "?"), search_text


def _run_group(label: str, questions: list[str], qdrant_filter) -> list[float]:
    print(f"\n{label}")
    print("─" * 96)
    print(f"{'score':>8}  {'top document':<36}  question")
    print("─" * 96)
    scores: list[float] = []
    for q in questions:
        score, doc, searched = top_score(q, qdrant_filter)
        # Show the corrected form when it differs, so an over-correction is
        # visible in the table rather than hidden behind a score.
        suffix = f"   [searched: {searched}]" if searched != q else ""
        if score is None:
            print(f"{'—':>8}  {'(no chunks returned)':<36}  {q}{suffix}")
            continue
        scores.append(score)
        print(f"{score:>8.3f}  {doc[:36]:<36}  {q}{suffix}")
    return scores


def _stats(label: str, scores: list[float]) -> None:
    if not scores:
        print(f"{label}: no scores")
        return
    print(
        f"{label}: n={len(scores)}  "
        f"min={min(scores):.3f}  median={statistics.median(scores):.3f}  max={max(scores):.3f}"
    )


async def _load_bm25() -> None:
    """
    Load the BM25 index into this process.

    The index lives in module-level memory and is normally populated by the API's
    startup hook. This script is a separate process, so without this the sparse
    half of hybrid retrieval is silently missing and every score measured here is
    dense-only — which is not what production does.
    """
    from ingestion.bm25_index import (
        build_index,
        count_active_chunks,
        get_index_size,
        load_index_from_disk,
    )
    from services.postgres_service import AsyncSessionLocal

    loaded = load_index_from_disk()
    async with AsyncSessionLocal() as db:
        active_chunks = await count_active_chunks(db)
        if not loaded or get_index_size() != active_chunks:
            await build_index(db)
    print(f"BM25 index ready: {get_index_size()} chunks (active in DB: {active_chunks})")


def main() -> None:
    asyncio.run(_load_bm25())
    qdrant_filter = _pilot_filter()

    in_scores = _run_group("IN-CORPUS (should route to documents)", IN_CORPUS, qdrant_filter)
    typo_scores = _run_group(
        "IN-CORPUS, MISSPELLED (must still route to documents)",
        IN_CORPUS_TYPOD,
        qdrant_filter,
    )
    gen_scores = _run_group("GENERAL (should route to the general LLM)", GENERAL, qdrant_filter)

    print("\n" + "=" * 96)
    print("SUMMARY")
    print("=" * 96)
    _stats("in-corpus", in_scores)
    _stats("typo'd   ", typo_scores)
    _stats("general  ", gen_scores)

    # Acceptance bar for typo tolerance: every misspelled in-corpus question
    # must stay above the floor. Below it, the router hands the question to
    # general knowledge and the documents are never consulted.
    floor = settings.router_rag_score_low
    fell_through = [s for s in typo_scores if s < floor]
    print()
    if not typo_scores:
        print("TYPO TOLERANCE: no scores collected.")
    elif fell_through:
        print(
            f"TYPO TOLERANCE: FAIL — {len(fell_through)} of {len(typo_scores)} misspelled "
            f"in-corpus questions fell below ROUTER_RAG_SCORE_LOW ({floor}): "
            f"{[round(s, 2) for s in fell_through]}"
        )
    else:
        print(
            f"TYPO TOLERANCE: PASS — all {len(typo_scores)} misspelled in-corpus questions "
            f"stayed above ROUTER_RAG_SCORE_LOW ({floor}); "
            f"weakest was {min(typo_scores):.3f}"
        )

    if not in_scores or not gen_scores:
        print("\nNot enough data to suggest thresholds.")
        return

    in_min, gen_max = min(in_scores), max(gen_scores)
    print()

    if in_min > gen_max:
        # Clean separation — put both thresholds inside the gap.
        gap = in_min - gen_max
        high = gen_max + gap * 0.66
        low = gen_max + gap * 0.33
        print(f"Clean separation. Gap = {gap:.3f} "
              f"(general tops out at {gen_max:.3f}, in-corpus bottoms out at {in_min:.3f})")
        print(f"  ROUTER_RAG_SCORE_HIGH={high:.2f}")
        print(f"  ROUTER_RAG_SCORE_LOW={low:.2f}")
    else:
        # Overlap — the blended band has to cover it.
        overlapping_general = [s for s in gen_scores if s >= in_min]
        overlapping_in = [s for s in in_scores if s <= gen_max]
        print(f"OVERLAP: {len(overlapping_general)} general question(s) score at or above the "
              f"weakest in-corpus question ({in_min:.3f}), and {len(overlapping_in)} in-corpus "
              f"question(s) score at or below the strongest general question ({gen_max:.3f}).")
        print("No clean split exists — widen the blended band to cover the overlap:")
        print(f"  ROUTER_RAG_SCORE_HIGH={max(in_scores) * 0.5:.2f}   (confident document hit)")
        print(f"  ROUTER_RAG_SCORE_LOW={min(gen_scores) + 0.5:.2f}   (below this: general only)")
        print("\nA large overlap usually means the corpus is too thin, not that the approach is "
              "wrong. Re-run this after more documents are ingested.")


if __name__ == "__main__":
    main()
