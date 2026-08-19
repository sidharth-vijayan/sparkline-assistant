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


@pytest.fixture
def store():
    """A store pointed at a throwaway collection, dropped afterwards."""
    try:
        s = session_store.SessionDocumentStore(collection_name=TEST_COLLECTION)
        s.ensure_collection()
    except Exception as e:                     # pragma: no cover
        pytest.skip(f"Qdrant unreachable: {e}")
    s.drop_collection()
    s.ensure_collection()
    yield s
    s.drop_collection()


def add(store, chat_id, user_id, n=2, uploaded_at=None, name="notes.docx"):
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


def test_session_chunks_do_not_land_in_the_corpus_collection(store):
    """The separate collection is the isolation mechanism — assert it holds."""
    add(store, "chat-1", "user-A", n=2)

    assert store.collection_name != settings.qdrant_collection_name
