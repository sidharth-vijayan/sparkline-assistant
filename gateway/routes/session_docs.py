"""
gateway/routes/session_docs.py
───────────────────────────────
Per-chat file attachments.

POST   /session/documents            — attach a file to a chat
GET    /session/documents            — list your own attachments
DELETE /session/documents/{id}       — withdraw your own attachment

GET    /admin/session-documents      — every attachment, for an administrator
DELETE /admin/session-documents/{id} — withdraw anyone's attachment

Two things distinguish these from the corpus routes in ingest.py:

  - An ordinary user may attach a file. Corpus ingestion is file-admin only,
    because a corpus document is visible to everyone; an attachment is visible
    only inside the chat it was attached to.
  - A user may only ever see or delete their own. The admin routes are separate
    endpoints rather than a flag, so a widened admin view can never be reached
    by an ordinary user passing an extra parameter.

Attachments never expire, so the admin listing is the only way anyone finds out
what is being held. The reconciliation sweep reclaims attachments whose chat is
gone; these endpoints are for the deliberate case.
"""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from db.models import User
from gateway.middleware.auth import get_current_admin, get_current_user
from ingestion.session_ingest import (
    MAX_SESSION_FILE_BYTES,
    SessionIngestError,
    UnsupportedSessionFile,
    ingest_session_document,
)
from services.session_store import SessionDocumentStore

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["session-documents"])


def _store() -> SessionDocumentStore:
    store = SessionDocumentStore()
    store.ensure_collection()
    return store


def _as_dict(doc) -> dict:
    return {
        "session_document_id": doc.session_document_id,
        "chat_id": doc.chat_id,
        "owner_user_id": doc.owner_user_id,
        "document_name": doc.document_name,
        "uploaded_at": doc.uploaded_at.isoformat(),
        "chunk_count": doc.chunk_count,
        "source_file_id": doc.source_file_id,
    }


# ── User-facing ───────────────────────────────────────────────────────────

@router.post("/session/documents", status_code=status.HTTP_201_CREATED)
async def attach_document(
    file: UploadFile = File(...),
    chat_id: str = Form(..., description="Open WebUI chat ID this belongs to"),
    source_file_id: Optional[str] = Form(
        None,
        description="Open WebUI file ID, so the pipe can avoid re-uploading it",
    ),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Attach a file to one chat. Retrievable only in that chat, by you."""
    if not chat_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_id is required — an attachment with no chat could "
                   "never be retrieved.",
        )

    contents = await file.read()
    if len(contents) > MAX_SESSION_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Attachments are limited to "
                   f"{MAX_SESSION_FILE_BYTES // (1024 * 1024)} MB.",
        )

    try:
        result = ingest_session_document(
            file_bytes=contents,
            filename=file.filename or "attachment",
            chat_id=chat_id.strip(),
            owner_user_id=str(current_user.id),
            store=_store(),
            source_file_id=source_file_id,
        )
    except UnsupportedSessionFile as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e
    except (SessionIngestError, ValueError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    logger.info(
        "session_docs.attached",
        user_id=str(current_user.id),
        chat_id=chat_id,
        document=result.document_name,
        chunks=result.chunk_count,
    )
    return {
        "session_document_id": result.session_document_id,
        "document_name": result.document_name,
        "chat_id": result.chat_id,
        "chunk_count": result.chunk_count,
        # Surfaced rather than hidden: a silently half-indexed attachment
        # answers questions from only part of the file.
        "truncated": result.truncated,
    }


@router.get("/session/documents")
async def list_my_documents(
    chat_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
) -> dict:
    """List your own attachments, optionally within one chat."""
    docs = _store().list_documents(
        owner_user_id=str(current_user.id), chat_id=chat_id
    )
    return {"documents": [_as_dict(d) for d in docs], "count": len(docs)}


@router.get("/session/documents/source-files")
async def my_source_files(
    chat_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Which Open WebUI files this chat already holds, for the pipe's dedupe.

    Scoped to the caller, so it cannot be used to discover what somebody else
    attached to a chat.
    """
    mine = {
        d.source_file_id
        for d in _store().list_documents(
            owner_user_id=str(current_user.id), chat_id=chat_id
        )
        if d.source_file_id
    }
    return {"chat_id": chat_id, "source_file_ids": sorted(mine)}


@router.delete("/session/documents/{session_document_id}")
async def delete_my_document(
    session_document_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Withdraw one of your own attachments.

    Ownership is re-checked here rather than trusted from the ID, so knowing
    another user's document ID is not enough to delete their file.
    """
    store = _store()
    owned = {
        d.session_document_id
        for d in store.list_documents(owner_user_id=str(current_user.id))
    }
    if session_document_id not in owned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such attachment.",
        )

    freed = store.delete_document(session_document_id)
    return {"deleted": session_document_id, "chunks_removed": freed}


# ── Administrator ─────────────────────────────────────────────────────────

@router.get("/admin/session-documents")
async def admin_list_documents(
    chat_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    _: User = Depends(get_current_admin),
) -> dict:
    """
    Every attachment currently held, filterable by chat or owner.

    Attachments have no expiry, so this listing is the only way to see what the
    store is holding.
    """
    docs = _store().list_documents(owner_user_id=owner_user_id, chat_id=chat_id)
    return {"documents": [_as_dict(d) for d in docs], "count": len(docs)}


@router.delete("/admin/session-documents/{session_document_id}")
async def admin_delete_document(
    session_document_id: str,
    admin: User = Depends(get_current_admin),
) -> dict:
    """Withdraw any attachment, regardless of owner."""
    freed = _store().delete_document(session_document_id)
    if freed == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such attachment.",
        )
    logger.info(
        "session_docs.admin_deleted",
        admin_id=str(admin.id),
        session_document_id=session_document_id,
        chunks=freed,
    )
    return {"deleted": session_document_id, "chunks_removed": freed}
