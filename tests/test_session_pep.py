"""
tests/test_session_pep.py
──────────────────────────
Unit tests for the session-attachment access filter.

This is the filter that decides whether one user's per-chat upload can be
retrieved by anybody else, so the assertions here are deliberately about the
exact conditions on the filter, not about "a filter was returned".

    poetry run pytest tests/test_session_pep.py
"""

import pytest

from access_control.pep import build_qdrant_filter, build_session_filter


def conditions(qfilter):
    """{payload key: matched value} for the filter's must-conditions."""
    return {c.key: c.match.value for c in qfilter.must}


def test_scopes_retrieval_to_one_chat():
    f = build_session_filter(chat_id="chat-abc", user_id="user-1")

    assert conditions(f)["chat_id"] == "chat-abc"


def test_scopes_retrieval_to_the_uploading_user():
    """Defence in depth: the chat ID alone would do, but a leaked or guessed
    chat ID must still not cross users."""
    f = build_session_filter(chat_id="chat-abc", user_id="user-1")

    assert conditions(f)["owner_user_id"] == "user-1"


def test_both_conditions_are_required_not_optional():
    f = build_session_filter(chat_id="chat-abc", user_id="user-1")

    assert {"chat_id", "owner_user_id"} <= set(conditions(f))
    assert f.should is None
    assert f.min_should is None


def test_refuses_to_build_a_filter_without_a_chat_id():
    """An unscoped session filter would match every attachment in the store,
    so it must be impossible to build one by accident."""
    with pytest.raises(ValueError):
        build_session_filter(chat_id="", user_id="user-1")


def test_refuses_to_build_a_filter_without_a_user_id():
    with pytest.raises(ValueError):
        build_session_filter(chat_id="chat-abc", user_id="")


def test_corpus_filter_is_untouched_by_session_scoping():
    """The corpus path must not gain session conditions — the separate
    collection is what keeps these two apart."""
    from unittest.mock import MagicMock
    from access_control.pdp import PDPDecision

    full_access = MagicMock()
    full_access.decision = PDPDecision.ALLOW
    full_access.full_access = True

    f = build_qdrant_filter(full_access)

    keys = {c.key for c in f.must}
    assert keys == {"is_active_version"}
    assert "chat_id" not in keys
    assert "owner_user_id" not in keys
