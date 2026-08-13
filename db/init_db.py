"""
db/init_db.py
─────────────
Create all tables and ensure the administrator account exists.
Run at initial setup:  python -m db.init_db

Pilot users are NOT seeded here — that roster changes, and reconciling it lives
in db/seed_pilot_users.py, which is idempotent and can be re-run safely:

    python -m db.seed_pilot_users --password '<password>' --apply

This script is safe to re-run: it creates the admin only if absent, and never
touches an existing account's password.
"""

import asyncio
import os
import uuid

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config.settings import get_settings
from db.models import Base, User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ADMIN_USERNAME = "file.admin"


async def init_db() -> None:
    engine = create_async_engine(settings.database_url, echo=False)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("[OK] Tables created.")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username == ADMIN_USERNAME)
        )
        if result.scalar_one_or_none() is not None:
            print(f"[OK] Admin '{ADMIN_USERNAME}' already exists — left untouched.")
        else:
            # Required rather than defaulted: an administrator account that can
            # create users and reset passwords must not ship with a known password.
            password = os.getenv("ADMIN_INITIAL_PASSWORD", "")
            if len(password) < settings.min_password_length:
                raise SystemExit(
                    "ADMIN_INITIAL_PASSWORD must be set to at least "
                    f"{settings.min_password_length} characters to create the admin account."
                )

            session.add(
                User(
                    id=uuid.uuid4(),
                    username=ADMIN_USERNAME,
                    full_name="File Administrator",
                    email="fileadmin@sparkline.co.in",
                    hashed_password=pwd_context.hash(password),
                    default_role="file_admin",
                    is_active=True,
                    is_admin=True,
                    is_file_admin=True,
                )
            )
            await session.commit()
            print(f"[OK] Created admin '{ADMIN_USERNAME}'.")

    await engine.dispose()
    print("\nNext: python -m db.seed_pilot_users --password '<password>' --apply")


if __name__ == "__main__":
    asyncio.run(init_db())
