"""
gateway/routes/ingest.py
─────────────────────────
Document ingestion endpoints (file-admin only).

POST   /admin/ingest              — upload a document with access control tags
GET    /admin/documents           — list all ingested documents
DELETE /admin/documents/{doc_id}  — withdraw a document and all of its versions
POST   /admin/rebuild-bm25        — rebuild the BM25 index from active chunks
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Chunk, Document, DocumentVersion, User
from gateway.middleware.auth import get_current_file_admin
from ingestion.embedder import embed_and_index_chunks
from ingestion.pipeline import IngestionPipeline
from services.postgres_service import get_db
from services.qdrant_service import delete_points

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin-ingestion"])

# Formats accepted at upload.
#
#   .xlsm            macro-enabled Excel. Macros are ignored; the sheet data
#                    reads exactly as .xlsx does, and real business spreadsheets
#                    are very often saved this way.
#   .doc / .xls      the pre-2007 binary formats, read via antiword and xlrd.
#                    Text is recovered, but .doc loses headings and tables, so
#                    re-saving as .docx still gives better answers.
#   .csv / .tsv      presented in the same tabular shape as a spreadsheet.
#   .txt / .md / .log  read directly, with encoding detection.
#
# Dispatch is by content, not extension: a misnamed file is routed on its actual
# signature, so a .xlsx someone renamed to .xls still opens.
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx", ".doc",
    ".xlsx", ".xlsm", ".xls",
    ".csv", ".tsv",
    ".txt", ".md", ".log",
}
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB


@router.post("/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    allowed_departments: Optional[str] = Form(
        None,
        description="Comma-separated department names (e.g. 'HR,Finance'). Leave blank for no restriction.",
    ),
    allowed_designations: Optional[str] = Form(
        None,
        description="Comma-separated designation names. Leave blank for no restriction.",
    ),
    is_public: bool = Form(
        False,
        description="If True, all users can access this document regardless of dept/designation.",
    ),
    current_user: User = Depends(get_current_file_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Upload and ingest a document.

    Only accessible by file-admin users.
    Parses, chunks, stores in MinIO + Postgres, embeds, and indexes in Qdrant.
    Triggers a BM25 index rebuild after ingestion.
    """
    # ── Validate file ──────────────────────────────────────────────
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    from pathlib import Path
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE_BYTES // 1024 // 1024}MB",
        )

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # ── Parse access control tags ──────────────────────────────────
    dept_list: Optional[list[str]] = None
    desig_list: Optional[list[str]] = None
    if allowed_departments:
        dept_list = [d.strip() for d in allowed_departments.split(",") if d.strip()]
    if allowed_designations:
        desig_list = [d.strip() for d in allowed_designations.split(",") if d.strip()]

    log = logger.bind(
        filename=file.filename,
        uploader=current_user.username,
        departments=dept_list,
        designations=desig_list,
        is_public=is_public,
    )
    log.info("ingest.start")

    # ── Run ingestion pipeline ─────────────────────────────────────
    pipeline = IngestionPipeline(db=db)

    try:
        doc, version, chunks = await pipeline.ingest(
            file_bytes=file_bytes,
            original_filename=file.filename,
            uploader_id=current_user.id,
            allowed_departments=dept_list,
            allowed_designations=desig_list,
            is_public=is_public,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("ingest.pipeline_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    # ── Embed + index in Qdrant ────────────────────────────────────
    # Get Qdrant point IDs from the chunk objects (set during pipeline.ingest)
    from db.models import Chunk
    from sqlalchemy import select as sa_select
    chunk_records_result = await db.execute(
        sa_select(Chunk).where(Chunk.document_version_id == version.id).order_by(Chunk.chunk_index)
    )
    chunk_records = chunk_records_result.scalars().all()
    qdrant_ids = [cr.qdrant_point_id for cr in chunk_records]

    # Find previous active version for soft-delete
    prev_version_result = await db.execute(
        sa_select(DocumentVersion)
        .where(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.id != version.id,
            DocumentVersion.is_active == False,  # noqa: E712
        )
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    prev_version = prev_version_result.scalar_one_or_none()

    try:
        embed_and_index_chunks(
            chunks=chunks,
            chunk_qdrant_ids=qdrant_ids,
            document_id=doc.id,
            document_version_id=version.id,
            document_name=file.filename,
            uploaded_at=version.uploaded_at,
            allowed_departments=dept_list,
            allowed_designations=desig_list,
            is_public=is_public,
            previous_version_id=prev_version.id if prev_version else None,
        )
    except Exception as e:
        logger.error("ingest.embedding_failed", error=str(e))
        # Don't roll back DB — file is in MinIO, metadata is saved
        # BM25 rebuild will still happen and Postgres record exists
        # The admin can re-trigger embedding if needed
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    # ── Rebuild BM25 index ─────────────────────────────────────────
    try:
        from ingestion.bm25_index import build_index
        from services.postgres_service import get_db_context
        import asyncio

        async def _rebuild_bm25_background() -> None:
            async with get_db_context() as bg_db:
                await build_index(bg_db)

        asyncio.create_task(_rebuild_bm25_background())
    except Exception as e:
        logger.warning("ingest.bm25_rebuild_failed", error=str(e))
        # BM25 rebuild failure is non-fatal — dense search still works

    log.info(
        "ingest.complete",
        document_id=str(doc.id),
        version_id=str(version.id),
        version_number=version.version_number,
        chunks=len(chunks),
    )

    response = {
        "message": "Document ingested successfully",
        "document_id": str(doc.id),
        "version_id": str(version.id),
        "version_number": version.version_number,
        "filename": file.filename,
        "chunks_created": len(chunks),
        "is_public": is_public,
        "allowed_departments": dept_list,
        "allowed_designations": desig_list,
    }

    # A file too large to index in full is still ingested, but the uploader has
    # to be told which parts are searchable. Silently indexing a fraction of a
    # document is the kind of failure nobody notices until an answer is missing.
    if pipeline.last_truncation:
        response["message"] = "Document ingested, but it was too large to index in full"
        response["truncated"] = pipeline.last_truncation

    return response


@router.get("/documents")
async def list_documents(
    current_user: User = Depends(get_current_file_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all ingested documents with their current version info."""
    result = await db.execute(
        select(Document).order_by(Document.updated_at.desc())
    )
    documents = result.scalars().all()

    return [
        {
            "document_id": str(doc.id),
            "filename": doc.filename,
            "is_public": doc.is_public,
            "allowed_departments": doc.allowed_departments,
            "allowed_designations": doc.allowed_designations,
            "current_version_id": str(doc.current_version_id) if doc.current_version_id else None,
            "updated_at": doc.updated_at.isoformat(),
        }
        for doc in documents
    ]


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_file_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Withdraw a document and every version of it from the assistant.

    The undo for an upload. Until now a document could be added but never
    removed, so a file uploaded to the wrong audience, or one that turned out to
    be the wrong draft, stayed answerable indefinitely.

    Order matters. The vectors go first: they are what retrieval actually reads,
    so removing them is what makes the document stop appearing in answers, and
    doing it before the database rows means a failure part-way through leaves
    orphaned rows — which are invisible to users — rather than orphaned vectors,
    which would keep being retrieved with no way left to identify them.

    The raw file in MinIO is deliberately kept, matching the pipeline's existing
    guarantee that an uploaded file is never destroyed. What is removed is the
    assistant's ability to retrieve and quote it; the original remains for audit.
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Document not found")

    doc = (
        await db.execute(select(Document).where(Document.id == doc_uuid))
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    log = logger.bind(
        document_id=document_id, filename=doc.filename, by=current_user.username
    )

    version_ids = (
        await db.execute(
            select(DocumentVersion.id).where(DocumentVersion.document_id == doc_uuid)
        )
    ).scalars().all()

    point_ids = [
        str(pid)
        for pid in (
            await db.execute(
                select(Chunk.qdrant_point_id).where(
                    Chunk.document_version_id.in_(version_ids)
                )
            )
        ).scalars().all()
    ] if version_ids else []

    try:
        delete_points(point_ids)
    except Exception as e:
        log.error("document_delete.qdrant_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Could not remove the document from the search index: {e}. "
                   "Nothing was deleted; please try again.",
        )

    # Break the document -> current_version_id reference before removing the
    # versions it points at, or the foreign key blocks the delete.
    await db.execute(
        update(Document).where(Document.id == doc_uuid).values(current_version_id=None)
    )
    if version_ids:
        await db.execute(delete(Chunk).where(Chunk.document_version_id.in_(version_ids)))
        await db.execute(
            delete(DocumentVersion).where(DocumentVersion.document_id == doc_uuid)
        )
    await db.execute(delete(Document).where(Document.id == doc_uuid))

    # Keyword search reads a separate in-memory index, so a document stays
    # answerable through it until this runs.
    try:
        from ingestion.bm25_index import build_index
        from services.postgres_service import get_db_context

        async def _rebuild_bm25_background() -> None:
            async with get_db_context() as bg_db:
                await build_index(bg_db)

        asyncio.create_task(_rebuild_bm25_background())
    except Exception as e:
        log.warning("document_delete.bm25_rebuild_failed", error=str(e))

    log.info(
        "document_delete.complete",
        versions_removed=len(version_ids),
        chunks_removed=len(point_ids),
    )
    return {
        "message": f"'{doc.filename}' has been removed from the assistant",
        "document_id": document_id,
        "filename": doc.filename,
        "versions_removed": len(version_ids),
        "chunks_removed": len(point_ids),
        "note": "The original file is retained in storage for audit; only the "
                "assistant's ability to retrieve and quote it has been removed.",
    }


@router.post("/rebuild-bm25")
async def rebuild_bm25(
    current_user: User = Depends(get_current_file_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually trigger a BM25 index rebuild from all active chunks in Postgres."""
    from ingestion.bm25_index import build_index, get_index_size

    await build_index(db)
    return {
        "message": "BM25 index rebuilt successfully",
        "corpus_size": get_index_size(),
    }
