"""
gateway/middleware/auth.py
───────────────────────────
JWT-based authentication middleware.

Issues JWT tokens on login; validates them on every API request.
User identity is extracted from the token and made available in
request.state.user_id and request.state.username for downstream use.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.models import User
from services.postgres_service import get_db

logger = structlog.get_logger(__name__)
settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, username: str) -> str:
    """Create a JWT access token for the given user."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns None if invalid."""
    try:
        return jwt.decode(
            token,
            settings.app_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency: extract and validate the current user from JWT.
    Raises 401 if the token is missing or invalid.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency: require admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def get_current_file_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency: require file-admin privileges."""
    if not (current_user.is_file_admin or current_user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="File admin privileges required",
        )
    return current_user
