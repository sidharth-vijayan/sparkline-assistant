"""
gateway/routes/exports.py
──────────────────────────
Download route for files the assistant generated.

GET /exports/{export_id}            — with a logged-in session
GET /exports/{export_id}?token=...  — with a single-file download token

Two ways in, because they serve different callers. An API client already holds a
session token and sends it as a header. A person clicking a link in the chat
window cannot send a header at all, so the link carries a scoped token instead.

Both paths end at the same check: the file is fetched under the owner's prefix,
so a request that names the wrong owner finds nothing. A missing export and
somebody else's export are deliberately the same 404 — an export id should not
be a way to learn that another person has a file.
"""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from gateway.middleware.auth import get_current_user_optional
from gateway.middleware.download_token import (
    DownloadTokenError,
    verify_download_token,
)
from services.export_store import ExportNotFound, load_export

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["exports"])


def _resolve_owner(export_id: str, token: Optional[str], current_user) -> str:
    """
    Work out whose export is being asked for.

    A download token wins when supplied, because that is the link case and the
    token names both the export and its owner. Otherwise fall back to the
    logged-in session.
    """
    if token:
        try:
            claims = verify_download_token(token, export_id=export_id)
        except DownloadTokenError as e:
            logger.warning("exports.bad_token", export_id=export_id, error=str(e))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This download link is not valid or has expired.",
            ) from e
        return str(claims["user_id"])

    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A download token or a logged-in session is required.",
        )
    return str(current_user.id)


@router.get("/exports/{export_id}")
async def download_export(
    export_id: str,
    token: Optional[str] = Query(
        None, description="Single-file download token, as sent in a chat link"
    ),
    current_user=Depends(get_current_user_optional),
) -> Response:
    """Return one generated file to its owner."""
    owner_id = _resolve_owner(export_id, token, current_user)

    try:
        export = load_export(user_id=owner_id, export_id=export_id)
    except ExportNotFound:
        # Same answer whether it never existed or belongs to somebody else.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such export.",
        )

    logger.info(
        "exports.downloaded",
        export_id=export_id,
        filename=export["filename"],
        bytes=export["size"],
        via="token" if token else "session",
    )
    return Response(
        content=export["data"],
        media_type=export["mime_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{export["filename"]}"',
            "Content-Length": str(export["size"]),
        },
    )
