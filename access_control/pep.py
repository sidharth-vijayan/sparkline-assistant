"""
access_control/pep.py
──────────────────────
Policy Enforcement Point (PEP) — translates a PDPResult into a Qdrant filter.

The filter is applied to EVERY vector search so restricted chunks are
never retrieved or shown to the LLM. This is enforcement at retrieval time,
not post-hoc filtering — restricted content never enters the LLM's context.

Filter logic:
  - full_access=True       → only filter for is_active_version=True (no access restrictions)
  - allow_public_only=True → is_public=True AND is_active_version=True
  - scoped access          → (dept match OR desig match OR is_public) AND is_active_version=True

The "is_active_version=True" condition is always included to enforce
non-destructive versioning: old chunk versions are excluded from all searches.
"""

from __future__ import annotations

import structlog
from qdrant_client.http import models as qmodels

from access_control.pdp import PDPDecision, PDPResult

logger = structlog.get_logger(__name__)


def build_qdrant_filter(pdp_result: PDPResult) -> qmodels.Filter | None:
    """
    Convert a PDP decision into a Qdrant filter dict.

    Args:
        pdp_result: Output from pdp.evaluate()

    Returns:
        Qdrant Filter object, or None if no search should be run (deny case).
        Callers must check for None and return 403 before calling retrieval.
    """
    if pdp_result.decision == PDPDecision.DENY:
        # PEP does not build a filter for denied requests.
        # The calling code must raise a 403 before querying Qdrant.
        return None

    # Active version filter — always applied
    active_filter = qmodels.FieldCondition(
        key="is_active_version",
        match=qmodels.MatchValue(value=True),
    )

    # ── Full access (admin or pilot_user) ─────────────────────────
    if pdp_result.full_access:
        return qmodels.Filter(must=[active_filter])

    # ── Public documents only ─────────────────────────────────────
    if pdp_result.allow_public_only:
        return qmodels.Filter(
            must=[
                active_filter,
                qmodels.FieldCondition(
                    key="is_public",
                    match=qmodels.MatchValue(value=True),
                ),
            ]
        )

    # ── Scoped access: dept + desig + public ─────────────────────
    # Allow if: document is public OR user dept matches OR user desig matches
    should_conditions: list[qmodels.Condition] = [
        qmodels.FieldCondition(
            key="is_public",
            match=qmodels.MatchValue(value=True),
        )
    ]

    if pdp_result.permitted_departments:
        should_conditions.append(
            qmodels.FieldCondition(
                key="allowed_departments",
                match=qmodels.MatchAny(any=pdp_result.permitted_departments),
            )
        )

    if pdp_result.permitted_designations:
        should_conditions.append(
            qmodels.FieldCondition(
                key="allowed_designations",
                match=qmodels.MatchAny(any=pdp_result.permitted_designations),
            )
        )

    return qmodels.Filter(
        must=[active_filter],
        min_should=qmodels.MinShould(
            conditions=should_conditions,
            min_count=1,
        ),
    )


def build_session_filter(chat_id: str, user_id: str) -> qmodels.Filter:
    """
    Build the retrieval filter for one chat's session attachments.

    This is deliberately a separate function from build_qdrant_filter() rather
    than a branch inside it. Session attachments live in their own Qdrant
    collection, so the corpus filter never has to know they exist, and no
    change here can widen what the corpus path returns.

    Both conditions are `must`. The chat ID alone would be enough to scope
    retrieval, but the owner check means a leaked or guessed chat ID still
    cannot surface another user's upload.

    Args:
        chat_id: The Open WebUI chat the attachment belongs to.
        user_id: The Sparkline user who uploaded it.

    Returns:
        A Qdrant Filter matching only this user's attachments in this chat.

    Raises:
        ValueError: If either identifier is missing. An unscoped session filter
            would match every attachment in the store, so building one by
            accident is made impossible rather than merely discouraged.
    """
    if not chat_id:
        raise ValueError("chat_id is required — an unscoped session filter would "
                         "match every attachment in the store")
    if not user_id:
        raise ValueError("user_id is required — an unscoped session filter would "
                         "match every user's attachments")

    return qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="chat_id",
                match=qmodels.MatchValue(value=chat_id),
            ),
            qmodels.FieldCondition(
                key="owner_user_id",
                match=qmodels.MatchValue(value=user_id),
            ),
        ]
    )
