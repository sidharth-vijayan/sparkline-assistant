"""
gateway/middleware/download_token.py
─────────────────────────────────────
Single-file download tokens for generated exports.

When the assistant produces a Word or Excel file, the chat gets a link to it. A
browser following a markdown link cannot send an Authorization header, so the
link has to carry its own proof of access.

That proof is deliberately narrow. The token names one export and one user, and
is checked against the export actually being requested, so a link that leaks
grants exactly one file rather than a session. It also carries an explicit
scope, so a login token cannot be used to fetch files and a download link
cannot be used to authenticate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from jose import JWTError, jwt

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

SCOPE = "export_download"

# Long enough that someone can come back to a file after a meeting, short
# enough that a link pasted somewhere does not stay live indefinitely. The file
# itself persists — asking again mints a fresh link.
DEFAULT_TTL = timedelta(hours=24)


class DownloadTokenError(Exception):
    """The token is missing, malformed, expired, or for a different export."""


def create_download_token(
    user_id: str,
    export_id: str,
    expires_in: timedelta | None = None,
) -> str:
    """Mint a token granting one user access to one export."""
    ttl = DEFAULT_TTL if expires_in is None else expires_in
    now = datetime.now(timezone.utc)
    payload = {
        "scope": SCOPE,
        "user_id": str(user_id),
        "export_id": str(export_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(
        payload, settings.app_secret_key, algorithm=settings.jwt_algorithm
    )


def verify_download_token(token: str, export_id: str) -> dict:
    """
    Check a token grants access to this specific export.

    Returns the claims. Raises DownloadTokenError for anything else — expired,
    tampered, wrong scope, or issued for a different export.
    """
    if not token:
        raise DownloadTokenError("no download token supplied")

    try:
        claims = jwt.decode(
            token, settings.app_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as e:
        raise DownloadTokenError(f"invalid download token: {e}") from e

    if claims.get("scope") != SCOPE:
        # A login token reaching this point would otherwise download files.
        raise DownloadTokenError("token is not a download token")

    if str(claims.get("export_id")) != str(export_id):
        logger.warning(
            "download_token.export_mismatch",
            requested=str(export_id),
            granted=str(claims.get("export_id")),
        )
        raise DownloadTokenError("token was issued for a different export")

    if not claims.get("user_id"):
        raise DownloadTokenError("token carries no owner")

    return claims
