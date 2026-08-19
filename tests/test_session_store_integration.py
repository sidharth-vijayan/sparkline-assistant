"""
tests/test_session_store_integration.py
────────────────────────────────────────
Integration tests for the session attachment store, against a real Qdrant.

Mocking Qdrant here would test nothing: the whole question is whether the
filters and the delete-by-filter actually behave the way we think against a
real index. Skips when Qdrant is unreachable so the unit suite stays offline.

    docker exec sparkline_api python -m pytest tests/test_session_store_integration.py -q
"""

from datetime import datetime, timedelta, timezone

import pytest

qdrant_client = pytest.importorskip("qdrant_client")

from config.settings import get_settings  # noqa: E402
from services import session_store  # noqa: E402

settings = get_settings()
VECTOR_SIZE = settings.qdrant_vector_size

TEST_COLLECTION = "sparkline_session_docs_pytest"


def vec(seed: float) -> list[float]:
    """A deterministic unit-ish vector, distinct per seed."""
    return [seed] + [0.0] * (VECTOR_SIZE - 1)


@pytest.fixture(scope="module")
def _store():
    """The throwaway collection, created once for the module.

    Deliberately not per-test: creating a collection means creating three
    payload indexes, and doing that for every test rate-limits Qdrant
    (ResourceExhausted). Tests get a clean slate from the `store` fixture
    below, which empties the collection rather than rebuilding it.
    """
    try:
        s = session_store.SessionDocumentStore(collection_name=TEST_COLLECTION)
        s.drop_collection()
        s.ensure_collection()
    except Exception as e:                     # pragma: no cover
        pytest.skip(f"Qdrant unreachable: {e}")
    yield s
    s.drop_collection()


@pytest.fixture
def store(_store):
    """A store holding nothing, for one test."""
    _store.delete_by_chat_ids([h.chat_id for h in _store.held_chats()])
    yield _store
    _store.delete_by_chat_ids([h.chat_id for h in _store.held_chats()])


def add(store, chat_id, user_id, n=2, uploaded_at=None, name="notes.docx",
        source_file_id=None):
    uploaded_at = uploaded_at or datetime.now(timezone.utc)
    chunks = [
        {"chunk_id": f"{chat_id}-{user_id}-{i}",
         "text": f"chunk {i} of {name}",
         "embedding": vec(0.1 * (i + 1)),
         "page_number": 1,
         "chunk_index": i}
        for i in range(n)
    ]
    return store.upsert_chunks(
        chunks=chunks, chat_id=chat_id, owner_user_id=user_id,
        document_name=name, uploaded_at=uploaded_at,
        source_file_id=source_file_id,
    )


# ── Isolation: the entire point of the feature ────────────────────────────

def test_a_users_attachment_is_retrievable_in_its_own_chat(store):
    add(store, "chat-1", "user-A")

    hits = store.search(query_vector=vec(0.1), chat_id="chat-1", user_id="user-A", top_k=10)

    assert len(hits) > 0


def test_another_user_cannot_retrieve_it_from_their_own_chat(store):
    add(store, "chat-1", "user-A")

    hits = store.search(query_vector=vec(0.1), chat_id="chat-2", user_id="user-B", top_k=10)

    assert hits == []


def test_the_same_user_cannot_retrieve_it_from_a_different_chat(store):
    """Per-chat, not per-user — an attachment must not follow someone around."""
    add(store, "chat-1", "user-A")

    hits = store.search(query_vector=vec(0.1), chat_id="chat-2", user_id="user-A", top_k=10)

    assert hits == []


def test_another_user_in_the_same_chat_cannot_retrieve_it(store):
    """Guards the owner condition specifically: same chat, different user."""
    add(store, "chat-1", "user-A")

    hits = store.search(query_vector=vec(0.1), chat_id="chat-1", user_id="user-B", top_k=10)

    assert hits == []


# ── What the sweep reads and acts on ──────────────────────────────────────

def test_held_chats_reports_each_chat_once_with_its_chunk_count(store):
    add(store, "chat-1", "user-A", n=3)
    add(store, "chat-2", "user-B", n=2)

    held = {h.chat_id: h for h in store.held_chats()}

    assert set(held) == {"chat-1", "chat-2"}
    assert held["chat-1"].chunk_count == 3
    assert held["chat-2"].chunk_count == 2


def test_held_chats_reports_the_newest_upload_for_a_chat(store):
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    new = datetime.now(timezone.utc) - timedelta(minutes=5)
    add(store, "chat-1", "user-A", n=1, uploaded_at=old, name="old.docx")
    add(store, "chat-1", "user-A", n=1, uploaded_at=new, name="new.docx")

    held = {h.chat_id: h for h in store.held_chats()}

    assert held["chat-1"].newest_upload_at >= new - timedelta(seconds=2)


def test_deleting_a_chat_removes_exactly_that_chats_chunks(store):
    add(store, "chat-doomed", "user-A", n=3)
    add(store, "chat-kept", "user-B", n=2)

    freed = store.delete_by_chat_ids(["chat-doomed"])

    remaining = {h.chat_id for h in store.held_chats()}
    assert remaining == {"chat-kept"}
    assert freed == 3


def test_deleting_nothing_is_a_no_op(store):
    add(store, "chat-1", "user-A", n=2)

    freed = store.delete_by_chat_ids([])

    assert freed == 0
    assert {h.chat_id for h in store.held_chats()} == {"chat-1"}


# ── The structural claim the whole design rests on ────────────────────────

def test_a_corpus_search_cannot_return_session_chunks(store):
    """The design's core claim: because every pilot user resolves to
    full_access, the corpus filter is only must=[is_active_version]. That
    filter would happily match a session chunk. What excludes it is that the
    corpus search is not looking in this collection at all — the ABSENCE of a
    session scope must EXCLUDE session chunks, not include them."""
    from access_control.pdp import PDPDecision, PDPResult
    from access_control.pep import build_qdrant_filter
    from services.qdrant_service import search_dense

    add(store, "chat-1", "user-A", n=3)

    # The real filter a pilot user gets: full access, no session scope at all.
    pilot = PDPResult(decision=PDPDecision.ALLOW, reason="pilot", full_access=True)
    corpus_filter = build_qdrant_filter(pilot)

    hits = search_dense(query_vector=vec(0.1), qdrant_filter=corpus_filter, top_k=50)

    returned_docs = {h["payload"].get("document_name") for h in hits}
    assert "notes.docx" not in returned_docs
    assert all("chat_id" not in (h["payload"] or {}) for h in hits)


def test_the_two_collections_are_actually_distinct(store):
    assert store.collection_name != settings.qdrant_collection_name


# ── Admin enumeration and deletion ────────────────────────────────────────

def test_lists_documents_with_the_metadata_an_admin_needs(store):
    """Owner, chat, filename and upload time — enough to answer 'who attached
    what, where, and when' without opening Qdrant by hand."""
    add(store, "chat-1", "user-A", n=3, name="budget.xlsx")

    docs = store.list_documents()

    assert len(docs) == 1
    doc = docs[0]
    assert doc.owner_user_id == "user-A"
    assert doc.chat_id == "chat-1"
    assert doc.document_name == "budget.xlsx"
    assert doc.chunk_count == 3
    assert doc.uploaded_at is not None
    assert doc.session_document_id


def test_lists_every_document_separately_even_within_one_chat(store):
    add(store, "chat-1", "user-A", n=2, name="first.docx")
    add(store, "chat-1", "user-A", n=4, name="second.docx")

    docs = store.list_documents()

    assert {d.document_name for d in docs} == {"first.docx", "second.docx"}
    assert {d.chunk_count for d in docs} == {2, 4}


def test_can_list_just_one_owners_documents(store):
    add(store, "chat-1", "user-A", n=2, name="mine.docx")
    add(store, "chat-2", "user-B", n=2, name="theirs.docx")

    docs = store.list_documents(owner_user_id="user-A")

    assert {d.document_name for d in docs} == {"mine.docx"}


def test_can_list_just_one_chats_documents(store):
    add(store, "chat-1", "user-A", n=2, name="here.docx")
    add(store, "chat-2", "user-A", n=2, name="elsewhere.docx")

    docs = store.list_documents(chat_id="chat-1")

    assert {d.document_name for d in docs} == {"here.docx"}


def test_deletes_a_single_document_leaving_the_rest_of_the_chat(store):
    """An admin withdrawing one file must not clear the whole conversation."""
    add(store, "chat-1", "user-A", n=2, name="keep.docx")
    add(store, "chat-1", "user-A", n=3, name="withdraw.docx")
    doomed = next(d for d in store.list_documents() if d.document_name == "withdraw.docx")

    freed = store.delete_document(doomed.session_document_id)

    assert freed == 3
    assert {d.document_name for d in store.list_documents()} == {"keep.docx"}


def test_deleting_an_unknown_document_removes_nothing(store):
    add(store, "chat-1", "user-A", n=2)

    freed = store.delete_document("00000000-0000-0000-0000-000000000000")

    assert freed == 0
    assert len(store.list_documents()) == 1


# ── Source file tracking, so the pipe can avoid re-uploading ──────────────

def test_records_the_source_file_id_it_was_uploaded_from(store):
    """Open WebUI hands the pipe its file list on every message in a chat, not
    just the turn the file was attached. Without a record of which of its files
    we already hold, the pipe would re-upload on every single message."""
    add(store, "chat-1", "user-A", n=2, source_file_id="owui-file-abc")

    doc = store.list_documents()[0]

    assert doc.source_file_id == "owui-file-abc"


def test_source_file_id_is_optional(store):
    """A direct API upload has no Open WebUI file behind it."""
    add(store, "chat-1", "user-A", n=1)

    assert store.list_documents()[0].source_file_id is None


def test_reports_which_source_files_a_chat_already_holds(store):
    add(store, "chat-1", "user-A", n=1, source_file_id="f1", name="one.txt")
    add(store, "chat-1", "user-A", n=1, source_file_id="f2", name="two.txt")
    add(store, "chat-2", "user-A", n=1, source_file_id="f3", name="other.txt")

    assert store.attached_source_file_ids("chat-1") == {"f1", "f2"}


def test_a_chat_holding_nothing_reports_no_source_files(store):
    assert store.attached_source_file_ids("chat-empty") == set()


def test_source_files_from_other_chats_are_not_reported(store):
    add(store, "chat-other", "user-A", n=1, source_file_id="elsewhere")

    assert store.attached_source_file_ids("chat-1") == set()
