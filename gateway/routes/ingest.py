"""
gateway/routes/ingest.py
─────────────────────────
Document ingestion endpoints (file-admin only).

POST /admin/ingest   — upload a document with access control tags
GET  /admin/documents — list all ingested documents
GET  /admin/documents/{doc_id} — document detail with version history
POST /admin/rebuild-bm25 — rebuild the BM25 index from active chunks
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Document, DocumentVersion, User
from gateway.middleware.auth import get_current_file_admin
from ingestion.embedder import embed_and_index_chunks
from ingestion.pipeline import IngestionPipeline
from services.postgres_service import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin-ingestion"])

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}
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

    return {
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
