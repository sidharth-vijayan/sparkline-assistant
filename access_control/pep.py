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
