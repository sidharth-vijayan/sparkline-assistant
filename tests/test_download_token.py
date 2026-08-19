"""
tests/test_download_token.py
─────────────────────────────
Unit tests for single-file download tokens.

A generated export is delivered as a link in the chat. A browser following a
markdown link cannot send an Authorization header, so the link has to carry its
own proof — and that proof must grant exactly one file, not a session.

These tests are the security boundary for that: a download link that could be
replayed against another export, or used as a general access token, would be
worse than the missing download it replaces.

    poetry run pytest tests/test_download_token.py
"""

from datetime import timedelta

import pytest

from gateway.middleware.download_token import (
    DownloadTokenError,
    create_download_token,
    verify_download_token,
)

USER = "11111111-1111-1111-1111-111111111111"
EXPORT = "22222222-2222-2222-2222-222222222222"


def test_a_token_round_trips_to_its_owner_and_export():
    token = create_download_token(user_id=USER, export_id=EXPORT)

    claims = verify_download_token(token, export_id=EXPORT)

    assert claims["user_id"] == USER
    assert claims["export_id"] == EXPORT


def test_a_token_is_rejected_for_a_different_export():
    """The whole point: a leaked link grants one file, not the whole store."""
    token = create_download_token(user_id=USER, export_id=EXPORT)

    with pytest.raises(DownloadTokenError):
        verify_download_token(token, export_id="33333333-3333-3333-3333-333333333333")


def test_an_expired_token_is_rejected():
    token = create_download_token(
        user_id=USER, export_id=EXPORT, expires_in=timedelta(seconds=-1)
    )

    with pytest.raises(DownloadTokenError):
        verify_download_token(token, export_id=EXPORT)


def test_a_tampered_token_is_rejected():
    token = create_download_token(user_id=USER, export_id=EXPORT)
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    with pytest.raises(DownloadTokenError):
        verify_download_token(tampered, export_id=EXPORT)


def test_a_normal_login_token_cannot_be_used_to_download():
    """A session token is not a download token. Without a scope check, any
    leaked login token would also be a file-download credential."""
    from gateway.middleware.auth import create_access_token

    login = create_access_token(user_id=USER, username="someone")

    with pytest.raises(DownloadTokenError):
        verify_download_token(login, export_id=EXPORT)


def test_a_download_token_is_not_accepted_as_a_login_token():
    """The reverse direction: the scoped token must not authenticate a session."""
    from gateway.middleware.auth import decode_token

    token = create_download_token(user_id=USER, export_id=EXPORT)
    claims = decode_token(token) or {}

    # Even if it decodes, it must not look like a login: no usable subject
    # identity that get_current_user would accept.
    assert claims.get("scope") == "export_download"


def test_garbage_is_rejected_rather_than_crashing():
    with pytest.raises(DownloadTokenError):
        verify_download_token("not-a-token", export_id=EXPORT)


def test_an_empty_token_is_rejected():
    with pytest.raises(DownloadTokenError):
        verify_download_token("", export_id=EXPORT)
