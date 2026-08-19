"""
tests/test_session_reconciliation.py
─────────────────────────────────────
Unit tests for the session-attachment reconciliation sweep.

The sweep compares the chats Open WebUI still holds against the chats we hold
attachments for, and deletes the difference. Getting that wrong deletes every
attachment in the system, so the decision logic is a pure function tested
directly here — no Qdrant, no SQLite, no clock.

    poetry run pytest tests/test_session_reconciliation.py
"""

from datetime import datetime, timedelta, timezone

import pytest

from maintenance.reconciliation import HeldChat, SweepPlan, plan_sweep

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
GRACE = timedelta(minutes=60)


def held(chat_id: str, age_minutes: int, chunks: int = 3) -> HeldChat:
    """An attachment we hold, uploaded age_minutes ago."""
    return HeldChat(
        chat_id=chat_id,
        newest_upload_at=NOW - timedelta(minutes=age_minutes),
        chunk_count=chunks,
    )


# ── The core behaviour ────────────────────────────────────────────────────

def test_deletes_attachments_whose_chat_no_longer_exists():
    plan = plan_sweep(
        held=[held("chat-gone", age_minutes=120)],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is False
    assert plan.delete == ("chat-gone",)


def test_keeps_attachments_whose_chat_still_exists():
    plan = plan_sweep(
        held=[held("chat-alive", age_minutes=120)],
        live_chat_ids={"chat-alive", "chat-other"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is False
    assert plan.delete == ()


def test_deletes_only_the_difference_when_both_kinds_are_present():
    plan = plan_sweep(
        held=[
            held("chat-alive", age_minutes=120),
            held("chat-gone", age_minutes=120),
            held("chat-also-gone", age_minutes=200),
        ],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is False
    assert sorted(plan.delete) == ["chat-also-gone", "chat-gone"]


# ── Safety rail: a broken enumeration must never delete ───────────────────

def test_aborts_when_the_chat_list_could_not_be_read():
    """live_chat_ids=None means the read failed. Deleting 'the difference'
    against an unknown live set would delete everything."""
    plan = plan_sweep(
        held=[held("chat-a", age_minutes=120), held("chat-b", age_minutes=120)],
        live_chat_ids=None,
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is True
    assert plan.delete == ()
    assert "unavailable" in plan.abort_reason


def test_aborts_when_the_chat_list_is_empty_but_we_hold_attachments():
    """An empty list is indistinguishable from a broken read, so it is refused
    rather than treated as 'every chat was deleted'."""
    plan = plan_sweep(
        held=[held("chat-a", age_minutes=120)],
        live_chat_ids=set(),
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is True
    assert plan.delete == ()
    assert "empty" in plan.abort_reason


def test_empty_live_list_is_fine_when_we_hold_nothing():
    plan = plan_sweep(held=[], live_chat_ids=set(), now=NOW, grace_period=GRACE)

    assert plan.aborted is False
    assert plan.delete == ()


# ── Safety rail: the upload/chat-creation race ────────────────────────────

def test_spares_a_recent_upload_whose_chat_is_not_visible_yet():
    """A file can be uploaded before Open WebUI has persisted the chat row.
    Inside the grace period that looks orphaned but is not."""
    plan = plan_sweep(
        held=[held("chat-brand-new", age_minutes=5)],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.aborted is False
    assert plan.delete == ()
    assert plan.spared_by_grace == ("chat-brand-new",)


def test_deletes_an_orphan_once_it_is_older_than_the_grace_period():
    plan = plan_sweep(
        held=[held("chat-gone", age_minutes=61)],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.delete == ("chat-gone",)
    assert plan.spared_by_grace == ()


# ── Safety rail: refuse an implausibly large sweep ────────────────────────

def test_aborts_when_the_sweep_would_delete_more_than_the_allowed_fraction():
    """A partial or corrupt chat read shows up as a huge orphan set. Refuse it
    rather than acting on a list we have reason to distrust."""
    plan = plan_sweep(
        held=[held(f"chat-{i}", age_minutes=120) for i in range(10)],
        live_chat_ids={"chat-0"},
        now=NOW,
        grace_period=GRACE,
        max_delete_fraction=0.5,
    )

    assert plan.aborted is True
    assert plan.delete == ()
    assert "fraction" in plan.abort_reason


def test_proceeds_when_the_sweep_stays_within_the_allowed_fraction():
    plan = plan_sweep(
        held=[held(f"chat-{i}", age_minutes=120) for i in range(10)],
        live_chat_ids={f"chat-{i}" for i in range(8)},
        now=NOW,
        grace_period=GRACE,
        max_delete_fraction=0.5,
    )

    assert plan.aborted is False
    assert sorted(plan.delete) == ["chat-8", "chat-9"]


def test_grace_spared_chats_do_not_count_toward_the_delete_fraction():
    """Eight recent uploads plus one real orphan is a 1-of-9 sweep, not 9-of-9."""
    plan = plan_sweep(
        held=[held(f"chat-new-{i}", age_minutes=5) for i in range(8)]
        + [held("chat-gone", age_minutes=120)],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
        max_delete_fraction=0.5,
    )

    assert plan.aborted is False
    assert plan.delete == ("chat-gone",)


# ── Reporting ─────────────────────────────────────────────────────────────

def test_plan_reports_the_chunk_count_it_would_free():
    plan = plan_sweep(
        held=[
            held("chat-gone", age_minutes=120, chunks=7),
            held("chat-also-gone", age_minutes=120, chunks=5),
            held("chat-alive", age_minutes=120, chunks=99),
        ],
        live_chat_ids={"chat-alive"},
        now=NOW,
        grace_period=GRACE,
    )

    assert plan.chunks_to_free == 12
