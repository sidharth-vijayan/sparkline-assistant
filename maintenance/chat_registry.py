"""
maintenance/chat_registry.py
─────────────────────────────
Which chats still exist, according to Open WebUI.

Open WebUI keeps its chats in its own SQLite database inside the
`sparkline_webui` container, so this is the one place in the codebase that
reaches into another service's storage. It is deliberately small and
read-only, and it is the only thing that needs changing if Open WebUI moves
its chats elsewhere.

The contract the sweep depends on:

    a set  → this is definitively the list of chats that exist
    None   → the list could not be read, for any reason at all

Never conflate the two. "No chats exist" and "I could not look" produce
identical orphan sets, and one of them is a reason to delete everything while
the other is a reason to do nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class OpenWebUIChatRegistry:
    """Reads live chat IDs out of Open WebUI's SQLite database, read-only."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    def live_chat_ids(self) -> set[str] | None:
        """
        Return the IDs of every chat that currently exists.

        Archived chats are included: archiving is not deleting, so an archived
        chat's attachments must survive.

        Returns None if the database is missing, unreadable, or not shaped the
        way we expect — the caller must treat that as "do not delete".
        """
        if not self._db_path.exists():
            logger.warning("chat_registry.missing", path=str(self._db_path))
            return None

        try:
            # mode=ro so we cannot write to another team's live database, and
            # immutable=0 so we still see their concurrent writes.
            uri = f"file:{self._db_path.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10) as con:
                rows = con.execute("SELECT id FROM chat").fetchall()
        except sqlite3.Error as e:
            logger.warning(
                "chat_registry.unreadable", path=str(self._db_path), error=str(e)
            )
            return None

        chat_ids = {str(row[0]) for row in rows if row[0]}
        logger.info("chat_registry.read", count=len(chat_ids))
        return chat_ids
