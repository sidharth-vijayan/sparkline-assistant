"""
db/seed_pilot_users.py
──────────────────────
Reconcile the pilot user roster with the database.

Run:  python -m db.seed_pilot_users --password 'Spark@@2026'

Idempotent by design — running it twice is a no-op, so it is safe to re-run
after a database restore or when someone is added to the pilot. This replaces
editing db/init_db.py and re-running it, which could only ever insert and
crashed on the second run.

What it does, in order:
  1. Creates any roster user that is missing.
  2. Corrects full name and email on roster users that already exist.
  3. Retires users who are no longer on the roster.

Passwords are never written into this file. The default password for newly
created accounts comes from --password or PILOT_DEFAULT_PASSWORD. Existing
users keep whatever password they have unless --reset-passwords is given, so a
re-run cannot silently undo someone's own password change.

Retiring is deliberately not a blind DELETE. A user who uploaded a document or
appears in the audit log is referenced by a non-nullable foreign key, and
deleting them would either fail outright or destroy the provenance of documents
and the audit trail. Those accounts are deactivated instead: they can no longer
log in or hold a session, but the history they own stays intact and attributable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config.settings import get_settings
from db.models import AuditLog, DocumentVersion, User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# The pilot roster. Usernames are the email local part verbatim — the Open WebUI
# pipeline identifies a user by their email address, so any divergence between
# these two columns becomes a failed login for that person.
PILOT_USERS = [
    {"username": "suraj.p",         "full_name": "Suraj P",         "email": "suraj.p@sparkline.co.in"},
    {"username": "shruti.gat",      "full_name": "Shruti Gat",      "email": "shruti.gat@sparkline.co.in"},
    {"username": "parag.g",         "full_name": "Parag G",         "email": "parag.g@sparkline.co.in"},
    {"username": "jayram.thombre",  "full_name": "Jayram Thombre",  "email": "jayram.thombre@sparkline.co.in"},
    {"username": "amogh.doshi",     "full_name": "Amogh Doshi",     "email": "amogh.doshi@sparkline.co.in"},
    {"username": "sandeep.p",       "full_name": "Sandeep P",       "email": "sandeep.p@sparkline.co.in"},
]

# Accounts that are not on the pilot roster but must survive a reconcile.
#   file.admin       — uploaded every document version (a non-nullable foreign
#                      key) and is the only administrator; removing it would
#                      orphan the corpus and leave no one able to ingest.
#   sidharth.vijayan — owns the entire audit history and needs to test.
PROTECTED_USERNAMES = {"file.admin", "sidharth.vijayan"}


async def _has_references(session: AsyncSession, user: User) -> bool:
    """True if any row elsewhere points at this user."""
    uploads = await session.execute(
        select(func.count(DocumentVersion.id)).where(DocumentVersion.uploaded_by == user.id)
    )
    if uploads.scalar_one():
        return True

    audits = await session.execute(
        select(func.count(AuditLog.id)).where(AuditLog.user_id == user.id)
    )
    return bool(audits.scalar_one())


async def seed(password: str, reset_passwords: bool, apply: bool) -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    created: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    deactivated: list[str] = []

    async with async_session() as session:
        result = await session.execute(select(User))
        existing = {u.username.lower(): u for u in result.scalars().all()}
        roster = {u["username"] for u in PILOT_USERS}

        # ── 1 & 2: create or correct roster users ───────────────────
        for spec in PILOT_USERS:
            user = existing.get(spec["username"])

            if user is None:
                session.add(
                    User(
                        id=uuid.uuid4(),
                        username=spec["username"],
                        full_name=spec["full_name"],
                        email=spec["email"],
                        hashed_password=pwd_context.hash(password),
                        department=None,
                        designation=None,
                        default_role=settings.default_pilot_role,
                        is_active=True,
                        is_admin=False,
                        is_file_admin=False,
                    )
                )
                created.append(spec["username"])
                continue

            changes = []
            if user.email != spec["email"]:
                user.email = spec["email"]
                changes.append("email")
            if user.full_name != spec["full_name"]:
                user.full_name = spec["full_name"]
                changes.append("full_name")
            if not user.is_active:
                user.is_active = True
                changes.append("reactivated")
            if reset_passwords:
                user.hashed_password = pwd_context.hash(password)
                changes.append("password")
            if changes:
                session.add(user)
                updated.append(f"{spec['username']} ({', '.join(changes)})")

        # ── 3: retire everyone else ─────────────────────────────────
        for username, user in existing.items():
            if username in roster or username in PROTECTED_USERNAMES:
                continue

            if await _has_references(session, user):
                if user.is_active:
                    user.is_active = False
                    session.add(user)
                    deactivated.append(username)
            else:
                await session.delete(user)
                deleted.append(username)

        if apply:
            await session.commit()
        else:
            await session.rollback()

    await engine.dispose()

    verb = "" if apply else " (dry run — nothing written)"
    print(f"\nPilot roster reconcile{verb}")
    print("─" * 60)
    for label, names in (
        ("created", created),
        ("updated", updated),
        ("deleted", deleted),
        ("deactivated", deactivated),
    ):
        print(f"  {label:12} {len(names):>2}  {', '.join(names) if names else '—'}")

    if not apply:
        print("\nRe-run with --apply to write these changes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile the pilot user roster.")
    parser.add_argument(
        "--password",
        default=os.getenv("PILOT_DEFAULT_PASSWORD", ""),
        help="Initial password for newly created users. Or set PILOT_DEFAULT_PASSWORD.",
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Also reset existing roster users to --password. Off by default so a "
             "re-run cannot undo a password someone has changed themselves.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the script only reports what it would do.",
    )
    args = parser.parse_args()

    # A password is always required: any roster user missing from the database
    # has to be created with one.
    if len(args.password) < settings.min_password_length:
        parser.error(
            f"--password must be at least {settings.min_password_length} characters "
            "(or set PILOT_DEFAULT_PASSWORD)"
        )

    return asyncio.run(seed(args.password, args.reset_passwords, args.apply))


if __name__ == "__main__":
    sys.exit(main())
