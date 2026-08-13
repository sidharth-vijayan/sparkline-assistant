"""
gateway/routes/admin.py
────────────────────────
Admin routes: user management, pilot user provisioning, audit log access.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.models import AuditLog, User
from gateway.middleware.auth import (
    create_access_token,
    get_current_admin,
    get_current_user,
    hash_password,
    verify_password,
)
from services.postgres_service import get_db

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(tags=["admin"])


# ── Shared helpers ───────────────────────────────────────────────────────────

async def _lookup_user(
    db: AsyncSession,
    username: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[User]:
    """
    Find a user by username, falling back to email.

    Both are compared case-insensitively and whitespace-trimmed: the identity
    reaching us comes from a chat front end, where a stray capital or a trailing
    space is a matter of how someone typed their name, not a different person.
    """
    if username:
        result = await db.execute(
            select(User).where(func.lower(User.username) == username.strip().lower())
        )
        user = result.scalar_one_or_none()
        if user is not None:
            return user

    if email:
        result = await db.execute(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        return result.scalar_one_or_none()

    return None


def _validate_password(password: str) -> None:
    """Reject passwords that are too short. Raises 422 rather than storing them."""
    if len(password or "") < settings.min_password_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must be at least {settings.min_password_length} characters",
        )


def _parse_user_id(user_id: str) -> uuid.UUID:
    """Turn a path parameter into a UUID, 404-ing rather than erroring at the driver."""
    try:
        return uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="User not found")


def _token_response(user: User) -> dict:
    """The shape returned by every route that issues a session."""
    return {
        "access_token": create_access_token(user_id=str(user.id), username=user.username),
        "token_type": "bearer",
        "user_id": str(user.id),
        "username": user.username,
        "is_admin": user.is_admin,
        "is_file_admin": user.is_file_admin,
    }


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authenticate a user and return a JWT token."""
    result = await db.execute(select(User).where(User.username == request.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    logger.info("auth.login_success", username=user.username)
    return _token_response(user)


# ── Service-token authentication (trusted front ends) ────────────────────────

class ServiceTokenRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None


@router.post("/auth/service-token")
async def issue_service_token(
    request: ServiceTokenRequest,
    x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Issue a session for a named user to a trusted front end.

    Open WebUI has already authenticated the person before their message reaches
    us, so the pipeline presents one service secret and names the user, rather
    than replaying that user's password. Two things follow from this that the
    password approach could not give us: users can change their own passwords
    without the pipeline knowing or breaking, and no user credential is stored
    in the pipeline at all.

    Fails closed. An unset SERVICE_TOKEN disables this route rather than
    accepting an empty secret, since a token that mints sessions for arbitrary
    users must never fall back to a guessable default.
    """
    expected = settings.service_token
    if not expected:
        logger.error("auth.service_token.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service-token authentication is not configured",
        )

    # Constant-time compare: a plain == leaks the secret's prefix by timing.
    if not x_service_token or not secrets.compare_digest(x_service_token, expected):
        logger.warning("auth.service_token.rejected", username=request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )

    if not request.username and not request.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username or email is required",
        )

    user = await _lookup_user(db, username=request.username, email=request.email)
    if user is None:
        logger.warning(
            "auth.service_token.unknown_user",
            username=request.username,
            email=request.email,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account exists for that user",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    logger.info("auth.service_token.issued", username=user.username)
    return _token_response(user)


# ── Password management ──────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Change your own password. Requires the current one.

    Safe to use at any time: the chat pipeline authenticates with a service
    token and never sees user passwords, so changing one does not interrupt
    anyone's session.
    """
    if not verify_password(request.current_password, current_user.hashed_password):
        logger.warning("auth.password_change_rejected", username=current_user.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    _validate_password(request.new_password)

    current_user.hashed_password = hash_password(request.new_password)
    db.add(current_user)

    logger.info("auth.password_changed", username=current_user.username)
    return {"message": "Password updated successfully"}


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    request: ResetPasswordRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reset another user's password (admin only) — for people who lock themselves out.

    Deliberately a separate route from PATCH /users/{id}: that one logs the
    fields it changed, and a password must never reach the logs.
    """
    result = await db.execute(select(User).where(User.id == _parse_user_id(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    _validate_password(request.new_password)

    user.hashed_password = hash_password(request.new_password)
    db.add(user)

    logger.info(
        "admin.password_reset",
        target_username=user.username,
        by=str(current_admin.id),
    )
    return {"message": "Password reset successfully", "username": user.username}


# ── User Management (admin only) ─────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    default_role: Optional[str] = None
    is_admin: bool = False
    is_file_admin: bool = False


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create a user (admin only).

    Provisioning previously existed only inside db/init_db.py, which meant
    adding one person required editing a seed script and re-running it against
    the database.
    """
    username = request.username.strip().lower()
    email = request.email.strip().lower() if request.email else None

    if not username:
        raise HTTPException(status_code=422, detail="username is required")

    _validate_password(request.password)

    if await _lookup_user(db, username=username) is not None:
        raise HTTPException(status_code=409, detail=f"User '{username}' already exists")
    if email and await _lookup_user(db, email=email) is not None:
        raise HTTPException(status_code=409, detail=f"Email '{email}' is already registered")

    user = User(
        id=uuid.uuid4(),
        username=username,
        email=email,
        full_name=request.full_name,
        hashed_password=hash_password(request.password),
        department=request.department,
        designation=request.designation,
        default_role=request.default_role or settings.default_pilot_role,
        is_active=True,
        is_admin=request.is_admin,
        is_file_admin=request.is_file_admin,
    )
    db.add(user)

    # Flush inside the request so a racing duplicate surfaces as a 409 here,
    # rather than as a 500 when the session commits on the way out.
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User already exists")

    logger.info("admin.user_created", username=username, by=str(current_admin.id))
    return {
        "message": "User created successfully",
        "user_id": str(user.id),
        "username": user.username,
        "email": user.email,
    }

class UpdateUserRequest(BaseModel):
    department: Optional[str] = None
    designation: Optional[str] = None
    default_role: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/users")
async def list_users(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all users (admin only)."""
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()

    return [
        {
            "user_id": str(u.id),
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "department": u.department,
            "designation": u.designation,
            "default_role": u.default_role,
            "is_active": u.is_active,
            "is_admin": u.is_admin,
            "is_file_admin": u.is_file_admin,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update a user's department, designation, or role.

    This is the endpoint used when real dept/designation data is
    provided for the pilot users, enabling the PDP/PEP to apply
    proper scoped access instead of the pilot stand-in.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if request.department is not None:
        user.department = request.department
    if request.designation is not None:
        user.designation = request.designation
    if request.default_role is not None:
        user.default_role = request.default_role
    if request.is_active is not None:
        user.is_active = request.is_active

    logger.info(
        "admin.user_updated",
        target_user_id=user_id,
        by=str(current_admin.id),
        changes=request.model_dump(exclude_none=True),
    )

    return {
        "message": "User updated successfully",
        "user_id": user_id,
        "department": user.department,
        "designation": user.designation,
        "default_role": user.default_role,
    }


# ── Current User Profile ─────────────────────────────────────────────────────

@router.get("/users/me")
async def get_me(current_user: User = Depends(get_current_user)) -> dict:
    """Return the currently authenticated user's profile."""
    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "department": current_user.department,
        "designation": current_user.designation,
        "default_role": current_user.default_role,
        "is_admin": current_user.is_admin,
        "is_file_admin": current_user.is_file_admin,
    }


# ── Audit Log ────────────────────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    limit: int = 100,
    agent_type: Optional[str] = None,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return recent audit log entries (admin only)."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if agent_type:
        query = query.where(AuditLog.agent_type == agent_type)

    result = await db.execute(query)
    entries = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "user_id": str(e.user_id) if e.user_id else None,
            "agent_type": e.agent_type,
            "query_text": e.query_text[:200],
            "pdp_decision": e.pdp_decision,
            "latency_ms": e.latency_ms,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]
