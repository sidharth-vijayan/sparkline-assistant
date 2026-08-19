"""
tests/test_chat_registry.py
────────────────────────────
Unit tests for the Open WebUI chat liveness adapter.

Uses a real SQLite file shaped like Open WebUI's, not a mock, because the whole
job of this adapter is to read that schema correctly. The contract that matters
most is the failure case: an unreadable database must return None, never an
empty set, or the sweep would treat "I couldn't look" as "every chat is gone".

    poetry run pytest tests/test_chat_registry.py
"""

import sqlite3

import pytest

from maintenance.chat_registry import OpenWebUIChatRegistry


@pytest.fixture
def webui_db(tmp_path):
    """A minimal stand-in for Open WebUI's webui.db."""
    path = tmp_path / "webui.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE chat ("
        " id VARCHAR PRIMARY KEY, user_id VARCHAR, title TEXT,"
        " created_at BIGINT, updated_at BIGINT, archived BOOLEAN)"
    )
    con.commit()
    con.close()
    return path


def add_chat(path, chat_id, user_id="user-1", archived=0):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO chat (id, user_id, title, created_at, updated_at, archived)"
        " VALUES (?, ?, ?, 0, 0, ?)",
        (chat_id, user_id, "a chat", archived),
    )
    con.commit()
    con.close()


def test_returns_the_ids_of_chats_that_exist(webui_db):
    add_chat(webui_db, "chat-a")
    add_chat(webui_db, "chat-b")

    registry = OpenWebUIChatRegistry(webui_db)

    assert registry.live_chat_ids() == {"chat-a", "chat-b"}


def test_an_archived_chat_still_counts_as_live(webui_db):
    """Archiving is not deleting — the chat is still there, so its attachments
    must survive the sweep."""
    add_chat(webui_db, "chat-archived", archived=1)

    registry = OpenWebUIChatRegistry(webui_db)

    assert registry.live_chat_ids() == {"chat-archived"}


def test_returns_an_empty_set_when_there_are_genuinely_no_chats(webui_db):
    registry = OpenWebUIChatRegistry(webui_db)

    assert registry.live_chat_ids() == set()


def test_returns_none_when_the_database_is_missing(tmp_path):
    """None, not an empty set. The sweep must be able to tell 'no chats' apart
    from 'I could not look'."""
    registry = OpenWebUIChatRegistry(tmp_path / "does-not-exist.db")

    assert registry.live_chat_ids() is None


def test_returns_none_when_the_database_is_unreadable(tmp_path):
    corrupt = tmp_path / "webui.db"
    corrupt.write_bytes(b"this is not a sqlite database")

    registry = OpenWebUIChatRegistry(corrupt)

    assert registry.live_chat_ids() is None


def test_returns_none_when_the_chat_table_is_absent(tmp_path):
    """Guards against an Open WebUI upgrade renaming the table out from under
    us — that must abort the sweep, not empty the store."""
    path = tmp_path / "webui.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE something_else (id VARCHAR)")
    con.commit()
    con.close()

    registry = OpenWebUIChatRegistry(path)

    assert registry.live_chat_ids() is None


def test_does_not_write_to_the_database(webui_db):
    """Opened read-only: this is another team's live database."""
    add_chat(webui_db, "chat-a")
    registry = OpenWebUIChatRegistry(webui_db)
    registry.live_chat_ids()

    con = sqlite3.connect(webui_db)
    remaining = con.execute("SELECT count(*) FROM chat").fetchone()[0]
    con.close()
    assert remaining == 1
