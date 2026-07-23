"""
db/init_db.py
─────────────
Utility to create all tables and seed the 10 pilot users.
Run once at initial setup:  python -m db.init_db
"""

import asyncio
import uuid
from passlib.context import CryptContext

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config.settings import get_settings
from db.models import Base, User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

PILOT_USERS = [
    {"username": "siddharth.doshi", "full_name": "Siddharth Doshi", "email": "siddharth.doshi@sparkline.in"},
    {"username": "shruti.doshi", "full_name": "Shruti Doshi", "email": "shruti.doshi@sparkline.in"},
    {"username": "sandeep.pansare", "full_name": "Sandeep Pansare", "email": "sandeep.pansare@sparkline.in"},
    {"username": "ajit.mahabare", "full_name": "Ajit Mahabare", "email": "ajit.mahabare@sparkline.in"},
    {"username": "amogh.doshi", "full_name": "Amogh Doshi", "email": "amogh.doshi@sparkline.in"},
    {"username": "parag.finance", "full_name": "Parag Finance", "email": "parag.finance@sparkline.in"},
    {"username": "suraj.finance", "full_name": "Suraj Finance", "email": "suraj.finance@sparkline.in"},
    {"username": "vikas.ranaware", "full_name": "Vikas Ranaware", "email": "vikas.ranaware@sparkline.in"},
    {"username": "yojana", "full_name": "Yojana", "email": "yojana@sparkline.in"},
    {"username": "roshni", "full_name": "Roshni", "email": "roshni@sparkline.in"},
]

DEFAULT_PILOT_PASSWORD = "Sparkline@2025"  # Change on first login


async def init_db() -> None:
    engine = create_async_engine(settings.database_url, echo=True)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✅ Tables created.")

    # Seed pilot users
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        for user_data in PILOT_USERS:
            user = User(
                id=uuid.uuid4(),
                username=user_data["username"],
                full_name=user_data["full_name"],
                email=user_data["email"],
                hashed_password=pwd_context.hash(DEFAULT_PILOT_PASSWORD),
                department=None,       # Not assigned yet — PDP uses default_role
                designation=None,      # Not assigned yet — PDP uses default_role
                default_role=settings.default_pilot_role,
                is_active=True,
                is_admin=False,
                is_file_admin=False,
            )
            session.add(user)

        # Seed a file-admin user
        admin = User(
            id=uuid.uuid4(),
            username="file.admin",
            full_name="File Administrator",
            email="fileadmin@sparkline.in",
            hashed_password=pwd_context.hash("FileAdmin@2025"),
            default_role="file_admin",
            is_active=True,
            is_admin=True,
            is_file_admin=True,
        )
        session.add(admin)

        await session.commit()
        print(f"✅ Seeded {len(PILOT_USERS)} pilot users + 1 file admin.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
