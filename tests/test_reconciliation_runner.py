"""
tests/test_reconciliation_runner.py
────────────────────────────────────
Tests for the sweep runner — the part that actually deletes.

plan_sweep decides; this decides nothing and only carries out what the plan
says. The tests that matter are the ones proving it does *not* delete: an
aborted plan and a dry run must both leave the store untouched.

Uses small in-memory fakes rather than mocks, so the assertions are about what
was actually removed from a store rather than about which calls were made.

    poetry run pytest tests/test_reconciliation_runner.py
"""

from datetime import datetime, timedelta, timezone

import pytest

from maintenance.reconciliation import HeldChat
from maintenance.sweep import SweepResult, run_sweep

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
GRACE = timedelta(minutes=60)


class FakeStore:
    """An in-memory stand-in for SessionDocumentStore."""

    def __init__(self, held: dict[str, int], age_minutes: int = 120):
        self._held = {
            chat_id: HeldChat(
                chat_id=chat_id,
                newest_upload_at=NOW - timedelta(minutes=age_minutes),
                chunk_count=chunks,
            )
            for chat_id, chunks in held.items()
        }
        self.deleted_calls: list[list[str]] = []

    def held_chats(self):
        return list(self._held.values())

    def delete_by_chat_ids(self, chat_ids):
        ids = [c for c in chat_ids if c]
        self.deleted_calls.append(ids)
        freed = sum(self._held[c].chunk_count for c in ids if c in self._held)
        for c in ids:
            self._held.pop(c, None)
        return freed

    @property
    def chat_ids(self):
        return set(self._held)


class FakeRegistry:
    def __init__(self, live):
        self._live = live

    def live_chat_ids(self):
        return self._live


def test_deletes_the_orphaned_chats_attachments():
    store = FakeStore({"chat-alive": 4, "chat-gone": 3})
    registry = FakeRegistry({"chat-alive"})

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE)

    assert store.chat_ids == {"chat-alive"}
    assert result.chunks_freed == 3
    assert result.aborted is False


def test_leaves_the_store_untouched_when_the_chat_list_is_unavailable():
    store = FakeStore({"chat-a": 2, "chat-b": 2})
    registry = FakeRegistry(None)

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE)

    assert store.chat_ids == {"chat-a", "chat-b"}
    assert store.deleted_calls == []
    assert result.aborted is True
    assert result.chunks_freed == 0


def test_leaves_the_store_untouched_when_the_chat_list_is_empty():
    store = FakeStore({"chat-a": 2})
    registry = FakeRegistry(set())

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE)

    assert store.chat_ids == {"chat-a"}
    assert store.deleted_calls == []
    assert result.aborted is True


def test_dry_run_reports_what_it_would_delete_without_deleting():
    store = FakeStore({"chat-alive": 4, "chat-gone": 3})
    registry = FakeRegistry({"chat-alive"})

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE, dry_run=True)

    assert store.chat_ids == {"chat-alive", "chat-gone"}
    assert store.deleted_calls == []
    assert result.dry_run is True
    assert result.would_delete == ("chat-gone",)
    assert result.chunks_freed == 0


def test_does_not_call_delete_at_all_when_there_is_nothing_to_delete():
    """An empty delete list must never reach the store — a stray unfiltered
    delete is the one call that could empty the collection."""
    store = FakeStore({"chat-alive": 4})
    registry = FakeRegistry({"chat-alive"})

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE)

    assert store.deleted_calls == []
    assert result.aborted is False
    assert result.chunks_freed == 0


def test_a_store_that_holds_nothing_is_a_clean_no_op():
    store = FakeStore({})
    registry = FakeRegistry({"chat-alive"})

    result = run_sweep(store, registry, now=NOW, grace_period=GRACE)

    assert result.aborted is False
    assert store.deleted_calls == []


def test_result_records_the_reason_when_a_rail_refused_the_sweep():
    store = FakeStore({f"chat-{i}": 1 for i in range(10)})
    registry = FakeRegistry({"chat-0"})

    result = run_sweep(
        store, registry, now=NOW, grace_period=GRACE, max_delete_fraction=0.5
    )

    assert result.aborted is True
    assert "fraction" in result.abort_reason
    assert store.deleted_calls == []
