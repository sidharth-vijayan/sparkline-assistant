"""
tests/test_session_merge.py
────────────────────────────
Unit tests for merging a chat's attachment chunks into the corpus candidate
pool before reranking.

The merge is pure so the interesting behaviour — shape normalisation, marking,
dedup, capping — is testable without Qdrant or a cross-encoder. Deciding which
merged candidate actually wins is the reranker's job, not this function's.

    poetry run pytest tests/test_session_merge.py
"""

import pytest

from retrieval.session_merge import merge_session_candidates


def corpus_chunk(text, name="policy.docx", score=0.5):
    return {
        "text": text,
        "document_name": name,
        "page_number": 2,
        "chunk_index": 0,
        "rrf_score": score,
        "qdrant_point_id": f"corpus-{text[:8]}",
        "is_active_version": True,
    }


def session_hit(text, name="attached.docx", score=0.9, chat="chat-1"):
    return {
        "qdrant_point_id": f"session-{text[:8]}",
        "score": score,
        "payload": {
            "text": text,
            "document_name": name,
            "page_number": 1,
            "chunk_index": 0,
            "chat_id": chat,
            "owner_user_id": "user-A",
            "session_document_id": "doc-1",
        },
    }


def test_returns_corpus_candidates_unchanged_when_there_is_no_attachment():
    corpus = [corpus_chunk("a"), corpus_chunk("b")]

    merged = merge_session_candidates(corpus, [])

    assert merged == corpus


def test_includes_the_attachment_chunks_alongside_the_corpus_ones():
    merged = merge_session_candidates([corpus_chunk("from corpus")],
                                      [session_hit("from attachment")])

    texts = {c["text"] for c in merged}
    assert texts == {"from corpus", "from attachment"}


def test_flattens_session_hits_into_the_corpus_candidate_shape():
    """The reranker and the citation builder both read a flat payload dict, so
    a session hit's nested 'payload' must not reach them."""
    merged = merge_session_candidates([], [session_hit("attached text")])

    chunk = merged[0]
    assert chunk["text"] == "attached text"
    assert chunk["document_name"] == "attached.docx"
    assert "payload" not in chunk


def test_marks_session_chunks_so_they_can_be_told_apart_downstream():
    """Citations need to say the answer came from the file you just attached,
    not from a Sparkline document everyone can see."""
    merged = merge_session_candidates([corpus_chunk("c")], [session_hit("s")])

    by_text = {c["text"]: c for c in merged}
    assert by_text["s"]["is_session_chunk"] is True
    assert by_text["c"].get("is_session_chunk", False) is False


def test_attachment_chunks_come_first():
    """Not a relevance decision — the reranker still scores everything. This
    only decides who survives the cap when there are more candidates than the
    reranker takes, and a file the user attached this turn should not be the
    thing dropped."""
    merged = merge_session_candidates(
        [corpus_chunk("c1"), corpus_chunk("c2")],
        [session_hit("s1")],
    )

    assert merged[0]["text"] == "s1"


def test_caps_the_combined_pool():
    corpus = [corpus_chunk(f"c{i}") for i in range(30)]
    session = [session_hit(f"s{i}") for i in range(5)]

    merged = merge_session_candidates(corpus, session, cap=10)

    assert len(merged) == 10


def test_the_cap_never_drops_an_attachment_in_favour_of_the_corpus():
    corpus = [corpus_chunk(f"c{i}") for i in range(30)]
    session = [session_hit(f"s{i}") for i in range(5)]

    merged = merge_session_candidates(corpus, session, cap=6)

    assert sum(c.get("is_session_chunk", False) for c in merged) == 5


def test_an_attachment_with_no_corpus_hits_still_produces_candidates():
    """The likely case for 'summarise this file' — retrieval finds nothing in
    the corpus, and the attachment is the whole answer."""
    merged = merge_session_candidates([], [session_hit("only source")])

    assert len(merged) == 1
    assert merged[0]["is_session_chunk"] is True


def test_identical_point_ids_are_not_duplicated():
    hit = session_hit("same")
    merged = merge_session_candidates([], [hit, hit])

    assert len(merged) == 1


# ── The gate-bypass decision ──────────────────────────────────────────────

def test_no_session_evidence_when_nothing_was_attached():
    from retrieval.session_merge import has_session_evidence

    assert has_session_evidence([corpus_chunk("a"), corpus_chunk("b")]) is False


def test_session_evidence_when_an_attachment_chunk_survived_reranking():
    """The bypass is evidence-based like the rest of the router: it fires
    because an attachment chunk is actually in play, not merely because a file
    exists somewhere in the chat."""
    from retrieval.session_merge import has_session_evidence

    merged = merge_session_candidates([corpus_chunk("c")], [session_hit("s")])

    assert has_session_evidence(merged) is True


def test_no_session_evidence_when_the_attachment_lost_the_rerank_entirely():
    from retrieval.session_merge import has_session_evidence

    survived_reranking = [corpus_chunk("c1"), corpus_chunk("c2")]

    assert has_session_evidence(survived_reranking) is False


def test_handles_an_empty_result_set():
    from retrieval.session_merge import has_session_evidence

    assert has_session_evidence([]) is False


# ── Reserved slots: attachments must not be crowded out ───────────────────

def scored(text, session=False, score=0.0):
    c = corpus_chunk(text) if not session else {
        "text": text, "document_name": "attached.docx",
        "qdrant_point_id": f"session-{text}", "is_session_chunk": True,
    }
    c["rerank_score"] = score
    return c


def test_without_an_attachment_it_is_just_the_top_k():
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"c{i}", score=10 - i) for i in range(10)]

    final = reserve_session_slots(ranked, final_k=5)

    assert [c["text"] for c in final] == ["c0", "c1", "c2", "c3", "c4"]


def test_an_attachment_that_lost_the_rerank_still_reaches_the_answer():
    """The bug this fixes: one attachment chunk against a 186-chunk corpus
    loses on a vague query like 'summarise this', gets truncated away, and the
    user is answered from documents they did not ask about."""
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"c{i}", score=10 - i) for i in range(10)]
    ranked.append(scored("the attachment", session=True, score=-99))

    final = reserve_session_slots(ranked, final_k=5)

    assert any(c.get("is_session_chunk") for c in final)
    assert "the attachment" in [c["text"] for c in final]


def test_reserves_several_slots_when_the_attachment_has_several_chunks():
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"c{i}", score=10 - i) for i in range(10)]
    ranked += [scored(f"s{i}", session=True, score=-50 - i) for i in range(4)]

    final = reserve_session_slots(ranked, final_k=5, reserved=3)

    assert sum(c.get("is_session_chunk", False) for c in final) == 3
    assert len(final) == 5


def test_never_reserves_more_slots_than_the_attachment_has_chunks():
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"c{i}", score=10 - i) for i in range(10)]
    ranked.append(scored("only one", session=True, score=-99))

    final = reserve_session_slots(ranked, final_k=5, reserved=3)

    assert sum(c.get("is_session_chunk", False) for c in final) == 1
    assert len(final) == 5


def test_an_attachment_that_wins_on_merit_is_not_penalised():
    """Reserving a floor must not become a ceiling."""
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"s{i}", session=True, score=100 - i) for i in range(5)]
    ranked += [scored(f"c{i}", score=1) for i in range(5)]

    final = reserve_session_slots(ranked, final_k=5, reserved=2)

    assert sum(c.get("is_session_chunk", False) for c in final) == 5


def test_results_stay_in_rerank_order():
    from retrieval.session_merge import reserve_session_slots

    ranked = [scored(f"c{i}", score=10 - i) for i in range(6)]
    ranked.append(scored("late", session=True, score=-99))

    final = reserve_session_slots(ranked, final_k=4)

    corpus_texts = [c["text"] for c in final if not c.get("is_session_chunk")]
    assert corpus_texts == sorted(corpus_texts, key=lambda t: int(t[1:]))
