"""
gateway/routes/admin.py
────────────────────────
Admin routes: user management, pilot user provisioning, audit log access.
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
router = APIRouter(tags=["admin"])


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

    token = create_access_token(user_id=str(user.id), username=user.username)
    logger.info("auth.login_success", username=user.username)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": str(user.id),
        "username": user.username,
        "is_admin": user.is_admin,
        "is_file_admin": user.is_file_admin,
    }


# ── User Management (admin only) ─────────────────────────────────────────────

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
