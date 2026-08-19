"""
maintenance/sweep.py
─────────────────────
Runs the reconciliation sweep.

Deliberately thin. Every decision about *what* to delete lives in
`plan_sweep()`, which is pure and exhaustively tested; this module only carries
out a plan it was handed. Keeping the judgement and the destruction in separate
places is what makes the dangerous half testable without a live Qdrant.

Two things it will not do, both load-bearing:

  - It never calls delete with an empty list. An unfiltered delete is the one
    call that could empty the collection, so the empty case returns early
    rather than relying on the store to no-op.
  - It never deletes on an aborted plan, for any of the three rail reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import structlog

from maintenance.reconciliation import HeldChat, plan_sweep

logger = structlog.get_logger(__name__)

DEFAULT_GRACE_PERIOD = timedelta(minutes=60)
DEFAULT_MAX_DELETE_FRACTION = 0.5


class AttachmentStore(Protocol):
    """The slice of SessionDocumentStore the sweep needs."""

    def held_chats(self) -> list[HeldChat]: ...
    def delete_by_chat_ids(self, chat_ids: list[str]) -> int: ...


class ChatRegistry(Protocol):
    """The slice of OpenWebUIChatRegistry the sweep needs."""

    def live_chat_ids(self) -> set[str] | None: ...


@dataclass(frozen=True)
class SweepResult:
    """What the sweep did. `would_delete` is populated on a dry run."""

    deleted: tuple[str, ...] = ()
    would_delete: tuple[str, ...] = ()
    spared_by_grace: tuple[str, ...] = ()
    chunks_freed: int = 0
    aborted: bool = False
    abort_reason: str | None = None
    dry_run: bool = False


def run_sweep(
    store: AttachmentStore,
    registry: ChatRegistry,
    now: datetime | None = None,
    grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    max_delete_fraction: float = DEFAULT_MAX_DELETE_FRACTION,
    dry_run: bool = False,
) -> SweepResult:
    """
    Reconcile held attachments against the chats that still exist.

    Args:
        store: Where attachments live.
        registry: Where the list of surviving chats comes from.
        now: Injected for testability; defaults to the current UTC time.
        grace_period: Attachments younger than this are never orphans.
        max_delete_fraction: Refuse a sweep larger than this share of what we
            hold. Defaults to half, so a partial chat read cannot clear the
            store even if it looks internally consistent.
        dry_run: Report the plan without deleting anything.

    Returns:
        A SweepResult describing what happened.
    """
    now = now or datetime.now(timezone.utc)

    held = store.held_chats()
    live = registry.live_chat_ids()

    plan = plan_sweep(
        held=held,
        live_chat_ids=live,
        now=now,
        grace_period=grace_period,
        max_delete_fraction=max_delete_fraction,
    )

    if plan.aborted:
        logger.warning(
            "sweep.aborted",
            reason=plan.abort_reason,
            held_chats=len(held),
            live_chats=None if live is None else len(live),
        )
        return SweepResult(
            spared_by_grace=plan.spared_by_grace,
            aborted=True,
            abort_reason=plan.abort_reason,
            dry_run=dry_run,
        )

    if not plan.delete:
        logger.info("sweep.nothing_to_do", held_chats=len(held))
        return SweepResult(spared_by_grace=plan.spared_by_grace, dry_run=dry_run)

    if dry_run:
        logger.info(
            "sweep.dry_run",
            would_delete=len(plan.delete),
            chunks=plan.chunks_to_free,
        )
        return SweepResult(
            would_delete=plan.delete,
            spared_by_grace=plan.spared_by_grace,
            dry_run=True,
        )

    freed = store.delete_by_chat_ids(list(plan.delete))
    logger.info(
        "sweep.complete",
        deleted_chats=len(plan.delete),
        chunks_freed=freed,
        spared_by_grace=len(plan.spared_by_grace),
    )
    return SweepResult(
        deleted=plan.delete,
        spared_by_grace=plan.spared_by_grace,
        chunks_freed=freed,
    )
