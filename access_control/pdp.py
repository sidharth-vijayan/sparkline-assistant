"""
access_control/pdp.py
──────────────────────
Policy Decision Point (PDP) — Week 6 core deliverable.

The PDP evaluates THREE inputs on every request and returns an allow/deny
decision plus the permitted resource scope:

  (a) User attributes   — department, designation, default_role (from PostgreSQL)
  (b) Request intent    — query intent category (from intent_classifier.py)
  (c) Resource scope    — the set of documents the user might be accessing

Decision logic (default-deny baseline):
  1. If user is not active → DENY
  2. If intent is ADMIN → require is_admin or is_file_admin flag → else DENY
  3. If user has department/designation → scope to matching documents + public docs
  4. If user has no department/designation (pilot phase) → scope to is_public docs only
     UNLESS default_role == 'pilot_user' → scope to all docs (full-access pilot mode)
  5. The permitted scope is expressed as Qdrant filter parameters passed to the PEP

NOTE: During pilot phase, the 10 named pilot users have default_role='pilot_user'
which grants full access. Real per-user restriction activates when dept/designation
are populated. This is documented explicitly and tested with synthetic test users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

from access_control.intent_classifier import QueryIntent
from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class PDPDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class UserAttributes:
    user_id: str
    username: str
    department: Optional[str]
    designation: Optional[str]
    default_role: str
    is_active: bool
    is_admin: bool
    is_file_admin: bool


@dataclass
class PDPResult:
    decision: PDPDecision
    reason: str
    # Permitted scope for Qdrant filtering (None means no restriction)
    permitted_departments: Optional[list[str]] = None
    permitted_designations: Optional[list[str]] = None
    allow_public_only: bool = False
    full_access: bool = False  # pilot_user or admin bypass


def evaluate(
    user: UserAttributes,
    intent: QueryIntent,
) -> PDPResult:
    """
    Evaluate the PDP decision for a given user + intent.

    Args:
        user: Pulled from PostgreSQL users table
        intent: Classified from the user's query

    Returns:
        PDPResult with allow/deny + permitted scope
    """
    log = logger.bind(user_id=user.user_id, intent=intent.value)

    # ── Rule 1: Inactive users are always denied ───────────────────
    if not user.is_active:
        log.warning("pdp.deny.inactive_user")
        return PDPResult(
            decision=PDPDecision.DENY,
            reason="User account is inactive",
        )

    # ── Rule 2: Admin intent requires admin flag ───────────────────
    if intent == QueryIntent.ADMIN:
        if user.is_admin or user.is_file_admin:
            log.info("pdp.allow.admin")
            return PDPResult(
                decision=PDPDecision.ALLOW,
                reason="Admin user with admin intent",
                full_access=True,
            )
        else:
            log.warning("pdp.deny.non_admin_admin_intent")
            return PDPResult(
                decision=PDPDecision.DENY,
                reason="Administrative operations require admin privileges",
            )

    # ── Rule 3: System admins get full access ──────────────────────
    if user.is_admin:
        log.info("pdp.allow.system_admin")
        return PDPResult(
            decision=PDPDecision.ALLOW,
            reason="System administrator — full access",
            full_access=True,
        )

    # ── Rule 4: Pilot users (no dept/designation yet) ─────────────
    if user.default_role == settings.default_pilot_role:
        if user.department is None and user.designation is None:
            # TEMPORARY: pilot_user gets full corpus access during pilot phase.
            # This stand-in is explicitly documented and will be replaced when
            # dept/designation attributes are supplied for each pilot user.
            log.info(
                "pdp.allow.pilot_user",
                note="Full access granted as temporary pilot stand-in. "
                     "Real restriction activates when dept/designation are set.",
            )
            return PDPResult(
                decision=PDPDecision.ALLOW,
                reason="Pilot phase: full access granted (no dept/designation assigned yet)",
                full_access=True,
            )

    # ── Rule 5: Users with dept/designation — scoped access ───────
    if user.department or user.designation:
        permitted_depts = [user.department] if user.department else None
        permitted_desigs = [user.designation] if user.designation else None

        log.info(
            "pdp.allow.scoped",
            department=user.department,
            designation=user.designation,
        )
        return PDPResult(
            decision=PDPDecision.ALLOW,
            reason=f"Scoped access: dept={user.department}, desig={user.designation}",
            permitted_departments=permitted_depts,
            permitted_designations=permitted_desigs,
            allow_public_only=False,
        )

    # ── Rule 6: No dept/designation, not a pilot user → public only ──
    log.info("pdp.allow.public_only", default_role=user.default_role)
    return PDPResult(
        decision=PDPDecision.ALLOW,
        reason="No department/designation — public documents only",
        allow_public_only=True,
    )
