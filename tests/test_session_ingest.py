"""
tests/test_session_ingest.py
─────────────────────────────
Unit tests for per-chat attachment ingestion.

The embedder is injected so these run without loading BAAI/bge-large-en — the
question here is whether a file becomes correctly-scoped chunks, not whether
sentence-transformers works. The store is a fake for the same reason; its real
behaviour is covered by the Qdrant integration tests.

    poetry run pytest tests/test_session_ingest.py
"""

import pytest

from ingestion.session_ingest import (
    SessionIngestError,
    UnsupportedSessionFile,
    ingest_session_document,
)


class FakeStore:
    """Captures what would have been written, without touching Qdrant."""

    def __init__(self):
        self.calls = []

    def upsert_chunks(self, chunks, chat_id, owner_user_id, document_name,
                      uploaded_at=None):
        self.calls.append({
            "chunks": chunks,
            "chat_id": chat_id,
            "owner_user_id": owner_user_id,
            "document_name": document_name,
        })
        return "session-doc-id-1"


def fake_embed(texts):
    """One distinct 1024-dim vector per text, no model involved."""
    return [[float(len(t))] + [0.0] * 1023 for t in texts]


def ingest(content=b"Widgets are frobnicated at 4471 units per cycle.",
           filename="notes.txt", chat_id="chat-1", user_id="user-A", store=None):
    store = store or FakeStore()
    result = ingest_session_document(
        file_bytes=content,
        filename=filename,
        chat_id=chat_id,
        owner_user_id=user_id,
        store=store,
        embed=fake_embed,
    )
    return result, store


# ── The happy path ────────────────────────────────────────────────────────

def test_a_text_file_becomes_chunks_in_the_session_store():
    _, store = ingest()

    assert len(store.calls) == 1
    assert len(store.calls[0]["chunks"]) >= 1


def test_chunks_are_scoped_to_the_chat_and_the_uploader():
    """The scoping is the entire security property — assert it at the point
    the chunks are written, not just in the filter that reads them back."""
    _, store = ingest(chat_id="chat-xyz", user_id="user-B")

    call = store.calls[0]
    assert call["chat_id"] == "chat-xyz"
    assert call["owner_user_id"] == "user-B"


def test_the_original_filename_is_kept_for_citations_and_admin_listing():
    _, store = ingest(filename="Q3 budget.txt")

    assert store.calls[0]["document_name"] == "Q3 budget.txt"


def test_every_chunk_carries_an_embedding():
    _, store = ingest()

    for chunk in store.calls[0]["chunks"]:
        assert len(chunk["embedding"]) == 1024


def test_returns_the_document_id_and_chunk_count():
    result, store = ingest()

    assert result.session_document_id == "session-doc-id-1"
    assert result.chunk_count == len(store.calls[0]["chunks"])
    assert result.document_name == "notes.txt"


# ── Rejections ────────────────────────────────────────────────────────────

def test_rejects_a_file_type_the_parsers_do_not_handle():
    with pytest.raises(UnsupportedSessionFile):
        ingest(filename="malware.exe")


def test_rejects_an_empty_file():
    with pytest.raises(SessionIngestError):
        ingest(content=b"")


def test_rejects_a_file_with_no_readable_text():
    with pytest.raises(SessionIngestError):
        ingest(content=b"   \n\n   \t  ")


def test_refuses_to_ingest_without_a_chat_id():
    """Without a chat ID the chunks would be unscoped, and the retrieval filter
    requires one — so an attachment that could never be read back, and could
    never be swept, must not be written in the first place."""
    with pytest.raises(ValueError):
        ingest(chat_id="")


def test_refuses_to_ingest_without_an_owner():
    with pytest.raises(ValueError):
        ingest(user_id="")


def test_nothing_is_written_when_ingestion_is_rejected():
    store = FakeStore()
    with pytest.raises(SessionIngestError):
        ingest(content=b"", store=store)

    assert store.calls == []


# ── Size limits: the only bound on growth, since nothing expires ──────────

def oversized_file() -> bytes:
    """Big enough to exceed MAX_SESSION_CHUNKS. Verified by the test below
    rather than assumed — an input that quietly stayed under the cap would make
    both truncation tests pass without exercising anything."""
    return "".join(
        "Sentence number %d concerns widget frobnication, zonk counts, "
        "snorkel calibration and the quarterly flimflam index.\n" % i
        for i in range(30000)
    ).encode()


def test_the_oversized_fixture_really_does_exceed_the_cap():
    """Guards the two tests below from silently becoming vacuous."""
    from ingestion.pipeline import parse_and_chunk
    from ingestion.session_ingest import MAX_SESSION_CHUNKS

    chunks = parse_and_chunk(oversized_file(), "huge.txt", ".txt")

    assert len(chunks) > MAX_SESSION_CHUNKS


def test_caps_the_number_of_chunks_one_attachment_can_contribute():
    """Attachments never expire, so an unbounded upload is permanent. The cap
    is what keeps a 100k-row spreadsheet from becoming a permanent resident."""
    from ingestion.session_ingest import MAX_SESSION_CHUNKS

    result, store = ingest(content=oversized_file(), filename="huge.txt")

    assert result.chunk_count == MAX_SESSION_CHUNKS
    assert len(store.calls[0]["chunks"]) == result.chunk_count


def test_reports_when_it_had_to_truncate():
    result, _ = ingest(content=oversized_file(), filename="huge.txt")

    assert result.truncated is True


def test_a_small_file_is_not_reported_as_truncated():
    result, _ = ingest()

    assert result.truncated is False
