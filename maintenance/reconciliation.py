"""
maintenance/reconciliation.py
──────────────────────────────
Reconciliation sweep for session attachments.

Session-uploaded documents have no TTL — they live as long as the chat they
were attached to. Nothing ages out on its own, so the only thing that reclaims
them is this sweep: compare the chats Open WebUI still holds against the chats
we hold attachments for, and delete the difference.

The decision is a pure function so it can be tested without Qdrant, SQLite or a
clock. That matters more here than usual, because the failure mode of a
reconciliation sweep is not "misses one file" — it is "deletes every file in
the system". Three rails guard against that, and each one fails closed:

  1. The chat list could not be read  → abort. An unknown live set makes every
     held attachment look orphaned.
  2. The chat list came back empty    → abort. Indistinguishable from a broken
     read, and "every chat was deleted" is not a plausible steady state.
  3. The sweep exceeds max_delete_fraction of what we hold → abort. A partial
     or truncated read shows up as an implausibly large orphan set.

A grace period covers the other direction: a file can be uploaded before Open
WebUI has persisted its chat row, so a young attachment is never an orphan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class HeldChat:
    """One chat we currently hold session attachments for."""

    chat_id: str
    newest_upload_at: datetime
    chunk_count: int = 0


@dataclass(frozen=True)
class SweepPlan:
    """What the sweep intends to do. Nothing has been deleted yet."""

    delete: tuple[str, ...] = ()
    spared_by_grace: tuple[str, ...] = ()
    chunks_to_free: int = 0
    aborted: bool = False
    abort_reason: str | None = None


def plan_sweep(
    held: list[HeldChat],
    live_chat_ids: set[str] | None,
    now: datetime,
    grace_period: timedelta,
    max_delete_fraction: float = 1.0,
) -> SweepPlan:
    """
    Decide which chats' attachments should be deleted.

    Args:
        held: Chats we hold attachments for, with their newest upload time.
        live_chat_ids: Chat IDs that still exist. None means the enumeration
            failed — distinct from an empty set, and both refuse to delete.
        now: Current time, injected so the rails are testable.
        grace_period: Attachments newer than this are never treated as orphans.
        max_delete_fraction: Refuse a sweep that would delete more than this
            fraction of the chats we hold. 1.0 disables the rail.

    Returns:
        A SweepPlan. Check `aborted` before acting on `delete`.
    """
    if live_chat_ids is None:
        return SweepPlan(
            aborted=True,
            abort_reason="chat list unavailable — refusing to delete",
        )

    if not held:
        return SweepPlan()

    if not live_chat_ids:
        return SweepPlan(
            aborted=True,
            abort_reason="chat list came back empty while attachments are held",
        )

    orphans = [h for h in held if h.chat_id not in live_chat_ids]

    spared = sorted(
        h.chat_id for h in orphans if now - h.newest_upload_at < grace_period
    )
    doomed = sorted(
        (h for h in orphans if now - h.newest_upload_at >= grace_period),
        key=lambda h: h.chat_id,
    )

    fraction = len(doomed) / len(held)
    if fraction > max_delete_fraction:
        return SweepPlan(
            spared_by_grace=tuple(spared),
            aborted=True,
            abort_reason=(
                f"sweep would delete {fraction:.0%} of held chats, above the "
                f"{max_delete_fraction:.0%} fraction ceiling"
            ),
        )

    return SweepPlan(
        delete=tuple(h.chat_id for h in doomed),
        spared_by_grace=tuple(spared),
        chunks_to_free=sum(h.chunk_count for h in doomed),
    )
