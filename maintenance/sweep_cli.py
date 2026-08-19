"""
maintenance/sweep_cli.py
─────────────────────────
Command-line entrypoint for the reconciliation sweep.

    # Report what would be removed, change nothing. Safe to run anywhere.
    docker exec sparkline_api python -m maintenance.sweep_cli

    # Actually delete.
    docker exec sparkline_api python -m maintenance.sweep_cli --apply

Dry run is the default deliberately. This command deletes user data based on
another service's state, so making the destructive form the one you have to ask
for costs a flag and removes a whole class of accident.

Reading Open WebUI's chat database requires it to be visible to this container.
It is NOT mounted by default — add to docker-compose.server.yml under `api`:

    volumes:
      - open_webui_data:/webui-data:ro

and pass --webui-db /webui-data/webui.db. Without it the sweep aborts rather
than deleting, which is the correct failure direction.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from config.settings import get_settings
from maintenance.chat_registry import OpenWebUIChatRegistry
from maintenance.sweep import (
    DEFAULT_GRACE_PERIOD,
    DEFAULT_MAX_DELETE_FRACTION,
    run_sweep,
)
from services.session_store import SessionDocumentStore

DEFAULT_WEBUI_DB = "/webui-data/webui.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maintenance.sweep_cli",
        description="Delete session attachments whose chat no longer exists.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the sweep only reports.",
    )
    parser.add_argument(
        "--webui-db",
        default=DEFAULT_WEBUI_DB,
        help=f"Path to Open WebUI's webui.db (default: {DEFAULT_WEBUI_DB})",
    )
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=int(DEFAULT_GRACE_PERIOD.total_seconds() // 60),
        help="Attachments younger than this are never treated as orphans.",
    )
    parser.add_argument(
        "--max-delete-fraction",
        type=float,
        default=DEFAULT_MAX_DELETE_FRACTION,
        help="Refuse a sweep larger than this share of held chats.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()

    store = SessionDocumentStore()
    store.ensure_collection()
    registry = OpenWebUIChatRegistry(args.webui_db)

    result = run_sweep(
        store=store,
        registry=registry,
        grace_period=timedelta(minutes=args.grace_minutes),
        max_delete_fraction=args.max_delete_fraction,
        dry_run=not args.apply,
    )

    print(f"collection      : {store.collection_name}")
    print(f"chat database   : {args.webui_db}")
    print(f"mode            : {'APPLY' if args.apply else 'dry run'}")

    if result.aborted:
        print(f"RESULT          : ABORTED — {result.abort_reason}")
        print("Nothing was deleted.")
        return 1

    if result.dry_run:
        print(f"would delete    : {len(result.would_delete)} chat(s)")
        for chat_id in result.would_delete:
            print(f"                  {chat_id}")
    else:
        print(f"deleted         : {len(result.deleted)} chat(s)")
        print(f"chunks freed    : {result.chunks_freed}")

    if result.spared_by_grace:
        print(
            f"spared (grace)  : {len(result.spared_by_grace)} chat(s) newer than "
            f"{args.grace_minutes}m"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
