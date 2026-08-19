"""
retrieval/session_merge.py
───────────────────────────
Fold a chat's attachment chunks into the corpus candidate pool.

Kept separate from hybrid_retrieval.py on purpose. The corpus pool is built by
RRF over two ranked lists that are both searching the same collection;
attachments come from a different collection, under a different filter, and are
not competing for the same ranks. Merging them here — after RRF, before
reranking — means the cross-encoder scores everything together and decides what
is actually relevant, rather than this module guessing.

The one ordering choice made here is that attachments come first, and it is not
a relevance claim. It only decides who survives the cap when there are more
candidates than the reranker will take, and a file the user attached this turn
should not be what gets dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Matches the reranker's candidate appetite in hybrid_retrieval.
DEFAULT_CANDIDATE_CAP = 40


def merge_session_candidates(
    corpus_candidates: list[dict],
    session_hits: list[dict],
    cap: int = DEFAULT_CANDIDATE_CAP,
) -> list[dict]:
    """
    Combine corpus candidates with this chat's attachment chunks.

    Args:
        corpus_candidates: Flat payload dicts from hybrid_search().
        session_hits: Raw {qdrant_point_id, score, payload} from
            SessionDocumentStore.search().
        cap: Maximum candidates to hand the reranker.

    Returns:
        One flat list in the corpus candidate shape. Session chunks carry
        is_session_chunk=True so citations can say the answer came from the
        file the user attached rather than from a shared document.
    """
    if not session_hits:
        return corpus_candidates

    flattened: list[dict] = []
    seen: set[str] = set()
    for hit in session_hits:
        point_id = str(hit.get("qdrant_point_id", ""))
        if point_id in seen:
            continue
        seen.add(point_id)

        payload = dict(hit.get("payload") or {})
        payload["qdrant_point_id"] = point_id
        # The session store scores by cosine similarity while the corpus pool
        # carries RRF scores. Neither survives reranking, but the key has to
        # exist because downstream code reads it.
        payload["rrf_score"] = hit.get("score", 0.0)
        payload["is_session_chunk"] = True
        flattened.append(payload)

    merged = flattened + [c for c in corpus_candidates
                          if str(c.get("qdrant_point_id", "")) not in seen]

    logger.info(
        "session_merge.complete",
        session_candidates=len(flattened),
        corpus_candidates=len(corpus_candidates),
        returned=min(len(merged), cap),
    )
    return merged[:cap]


DEFAULT_RESERVED_SLOTS = 3


def reserve_session_slots(
    reranked: list[dict],
    final_k: int,
    reserved: int = DEFAULT_RESERVED_SLOTS,
) -> list[dict]:
    """
    Keep the top `final_k` results, guaranteeing attachments a share of them.

    Reranking alone is not enough. A vague question — "summarise this", the most
    natural thing to ask about a file you just attached — gives the
    cross-encoder nothing to match on, so a single attachment chunk competing
    against a whole corpus is simply outnumbered and truncated away. The user
    then gets an answer drawn from documents they did not ask about, with no
    sign their file was ignored. That was observed, not hypothesised.

    So attachments get a floor, not a boost: up to `reserved` of the final
    slots, filled by the best-reranked attachment chunks. It is only a floor —
    an attachment that wins on merit takes as many slots as it earns.

    Args:
        reranked: All candidates, rerank_score attached, best first.
        final_k: How many results to return.
        reserved: Slots guaranteed to attachments when any are present.
    """
    session = [c for c in reranked if c.get("is_session_chunk")]
    if not session:
        return reranked[:final_k]

    keep_session = session[:min(reserved, len(session), final_k)]
    kept_ids = {id(c) for c in keep_session}

    # Fill what is left in rerank order, skipping anything already reserved.
    remaining = [c for c in reranked if id(c) not in kept_ids]
    final = keep_session + remaining[: max(0, final_k - len(keep_session))]

    # Restore rerank order so the prompt sees the strongest evidence first,
    # whichever collection it came from.
    final.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)

    logger.info(
        "session_merge.slots_reserved",
        session_kept=len(keep_session),
        returned=len(final),
    )
    return final


def has_session_evidence(chunks: list[dict]) -> bool:
    """
    Whether an attachment chunk actually survived into the final results.

    This is what lets the evidence gate be bypassed for an attached file. It
    deliberately asks whether an attachment is *in play*, not whether the chat
    *has* an attachment: "summarise this" retrieves the attachment and should
    answer from it, while a later unrelated question in the same chat should
    still be free to route to general knowledge.

    Without the bypass, a weak retrieval query like "summarise this" can score
    below ROUTER_RAG_SCORE_LOW and be answered from general knowledge — which
    looks, to the person who just attached a file, like the file was ignored.
    """
    return any(c.get("is_session_chunk") for c in chunks)


@dataclass
class SessionContext:
    """
    One chat's attachments, as the retrieval path needs them.

    Carries the scope (which chat, whose) rather than a prebuilt filter, so the
    filter is constructed by build_session_filter() at the point of use and
    cannot be passed around half-formed.
    """

    chat_id: str
    user_id: str
    store: object = None
    top_k: int = 10

    def search(self, text: str) -> list[dict]:
        """Embed the query and fetch this chat's attachment chunks."""
        from services.embedding_service import embed_query

        store = self.store or _default_store()
        try:
            return store.search(
                query_vector=embed_query(text),
                chat_id=self.chat_id,
                user_id=self.user_id,
                top_k=self.top_k,
            )
        except Exception as e:
            # An attachment lookup that fails must degrade to a corpus-only
            # answer, not take the whole question down with it.
            logger.warning(
                "session_merge.lookup_failed", chat_id=self.chat_id, error=str(e)
            )
            return []


def _default_store():
    from services.session_store import SessionDocumentStore

    return SessionDocumentStore()
