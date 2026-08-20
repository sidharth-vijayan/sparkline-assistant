"""
ingestion/pipeline.py
──────────────────────
Top-level ingestion orchestrator.

Called once per file upload. Executes:
  1. Parse the raw file bytes (using appropriate parser)
  2. Run OCR for any pages flagged as scanned
  3. Chunk all content into TextChunk objects
  4. Store raw file in MinIO (permanent, non-destructive)
  5. Record Document + DocumentVersion in PostgreSQL
  6. Store Chunk records in PostgreSQL
  7. Generate embeddings via EmbeddingService
  8. Upsert vectors into Qdrant with full metadata payload
  9. Update the BM25 index with new chunks

If a document with the same filename already exists, a new version is
created. The previous version's Qdrant chunks are soft-deactivated
(they remain in Qdrant but will be filtered out by the PEP via
version_id metadata filtering). The old MinIO object is NEVER deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.models import Chunk, Document, DocumentVersion
from ingestion.chunker import (
    chunk_docx_blocks,
    chunk_excel_sheets,
    chunk_pdf_pages,
    chunk_text,
    TextChunk,
)
from ingestion.parsers.docx_parser import parse_docx
from ingestion.parsers.excel_parser import parse_excel
from ingestion.parsers.ocr_parser import ocr_pdf_page
from ingestion.parsers.pdf_parser import parse_pdf
from ingestion.parsers.text_parser import parse_csv, parse_text
from services.minio_service import upload_document

logger = structlog.get_logger(__name__)
settings = get_settings()


class IngestionPipeline:
    """
    Orchestrates end-to-end document ingestion.

    Embedding and Qdrant upsert are deferred to after this class runs,
    so the pipeline can be tested independently of the embedding service.
    Use run_full_ingestion() for the all-in-one path.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        # Set by ingest() when a document was too large to index in full, so the
        # route can tell the uploader instead of silently indexing part of it.
        self.last_truncation: Optional[dict] = None

    async def ingest(
        self,
        file_bytes: bytes,
        original_filename: str,
        uploader_id: uuid.UUID,
        allowed_departments: Optional[list[str]] = None,
        allowed_designations: Optional[list[str]] = None,
        is_public: bool = False,
    ) -> tuple[Document, DocumentVersion, list[TextChunk]]:
        """
        Parse, chunk, store metadata for a newly uploaded document.

        Returns the Document, DocumentVersion, and list of TextChunks
        (the caller is responsible for embedding + Qdrant upsert).
        """
        suffix = Path(original_filename).suffix.lower()
        log = logger.bind(filename=original_filename, uploader=str(uploader_id))

        # ── Step 1: Parse ─────────────────────────────────────────
        log.info("ingestion.parse.start")
        chunks: list[TextChunk] = await self._parse_and_chunk(
            file_bytes, original_filename, suffix
        )
        log.info("ingestion.parse.done", chunk_count=len(chunks))

        if not chunks:
            raise ValueError(f"No content could be extracted from '{original_filename}'")

        # ── Step 1b: Cap how much of one file may enter the index ──
        chunks, self.last_truncation = _cap_chunks(
            chunks, settings.ingest_max_chunks_per_document
        )
        if self.last_truncation:
            log.warning(
                "ingestion.truncated",
                kept=self.last_truncation["chunks_indexed"],
                produced=self.last_truncation["chunks_produced"],
            )

        # ── Step 2: MinIO upload ───────────────────────────────────
        doc_id = uuid.uuid4()
        version_id = uuid.uuid4()
        mime_type = _mime_for_suffix(suffix)

        minio_key = upload_document(
            file_data=file_bytes,
            document_id=doc_id,
            version_id=version_id,
            original_filename=original_filename,
            content_type=mime_type,
        )
        log.info("ingestion.minio.uploaded", object_key=minio_key)

        # ── Step 3: Check for existing document (same filename) ────
        existing_doc = await self._find_existing_document(original_filename)

        if existing_doc:
            doc = existing_doc
            # Deactivate the previous active version in Postgres
            await self.db.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.is_active == True,  # noqa: E712
                )
                .values(is_active=False)
            )
            # Calculate next version number
            result = await self.db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            last_version = result.scalar_one_or_none()
            next_version_num = (last_version.version_number + 1) if last_version else 1
            log.info("ingestion.new_version", version_number=next_version_num)
        else:
            # Brand new document
            doc = Document(
                id=doc_id,
                filename=original_filename,
                original_filename=original_filename,
                allowed_departments=allowed_departments,
                allowed_designations=allowed_designations,
                is_public=is_public,
            )
            self.db.add(doc)
            await self.db.flush()  # Insert document with current_version_id = None
            next_version_num = 1
            log.info("ingestion.new_document")

        # ── Step 4: Create DocumentVersion ─────────────────────────
        version = DocumentVersion(
            id=version_id,
            document_id=doc.id,
            version_number=next_version_num,
            minio_object_key=minio_key,
            file_size_bytes=len(file_bytes),
            mime_type=mime_type,
            uploaded_by=uploader_id,
            uploaded_at=datetime.now(timezone.utc),
            is_active=True,
        )
        self.db.add(version)
        await self.db.flush()  # Insert version pointing to document

        # Update document's current_version pointer
        doc.current_version_id = version.id
        doc.allowed_departments = allowed_departments
        doc.allowed_designations = allowed_designations
        doc.is_public = is_public
        await self.db.flush()  # Update document with current_version_id

        # ── Step 5: Store Chunk records in Postgres ────────────────
        db_chunks: list[Chunk] = []
        for tc in chunks:
            chunk_record = Chunk(
                id=uuid.uuid4(),
                document_version_id=version_id,
                qdrant_point_id=uuid.uuid4(),
                chunk_index=tc.chunk_index,
                page_number=tc.page_number,
                text=tc.text,
                token_count=tc.token_count,
            )
            db_chunks.append(chunk_record)
            self.db.add(chunk_record)

        await self.db.flush()

        # Attach Qdrant point IDs back to TextChunks for the embedder
        for tc, db_chunk in zip(chunks, db_chunks):
            tc.__dict__["qdrant_point_id"] = db_chunk.qdrant_point_id

        log.info("ingestion.db.flushed", chunk_count=len(db_chunks))
        return doc, version, chunks

    async def _parse_and_chunk(
        self, file_bytes: bytes, filename: str, suffix: str
    ) -> list[TextChunk]:
        """Dispatch to the appropriate parser + chunker based on file type."""
        return parse_and_chunk(file_bytes, filename, suffix)

    async def _find_existing_document(self, filename: str) -> Optional[Document]:
        """
        Look up an existing document by original filename.

        Was stranded inside parse_and_chunk() when that function was extracted
        to module level: the body moved out of the class and took this method
        with it, where its four-space indent made it a nested function that
        nothing could call. ingest() calls self._find_existing_document() on
        every upload, so document ingestion raised AttributeError for any file
        at all. Nothing caught it because no test exercises ingest() end to end.
        """
        result = await self.db.execute(
            select(Document).where(Document.original_filename == filename).limit(1)
        )
        return result.scalar_one_or_none()


def parse_and_chunk(
    file_bytes: bytes, filename: str, suffix: str
) -> list[TextChunk]:
    """
    Turn an uploaded file into chunks, by file type.

    Module-level rather than a pipeline method because per-chat attachments go
    through the same parsers and chunker but never touch Postgres, so they have
    no IngestionPipeline to call it on. Behaviour is unchanged for the corpus
    path, which now delegates here.
    """
    if suffix == ".pdf":
        pages = parse_pdf(file_bytes, filename)
        # Run OCR for any pages that need it
        for page in pages:
            if page.extraction_method == "ocr_needed":
                ocr_result = ocr_pdf_page(file_bytes, page.page_number - 1)
                page.text = ocr_result.text
                page.extraction_method = "ocr"
        return chunk_pdf_pages(pages)

    elif suffix in (".doc", ".docx"):
        blocks = parse_docx(file_bytes, filename)
        return chunk_docx_blocks(blocks)

    elif suffix in (".xls", ".xlsx", ".xlsm"):
        sheets = parse_excel(file_bytes, filename)
        return chunk_excel_sheets(sheets)

    elif suffix in (".csv", ".tsv"):
        parsed = parse_csv(file_bytes, filename)
        if not parsed.text.strip():
            raise ValueError(f"'{filename}' contains no readable rows")
        return chunk_text(parsed.text, page_number=1)

    elif suffix in (".txt", ".md", ".log"):
        parsed = parse_text(file_bytes, filename)
        if not parsed.text.strip():
            raise ValueError(f"'{filename}' is empty")
        return chunk_text(parsed.text, page_number=1)

    else:
        raise ValueError(
            f"Unsupported file type: '{suffix}'. "
            "Supported: .pdf, .docx, .doc, .xlsx, .xlsm, .xls, .csv, .txt, .md"
        )


def _cap_chunks(
    chunks: list[TextChunk], budget: int
) -> tuple[list[TextChunk], Optional[dict]]:
    """
    Limit how many chunks one document contributes to the index.

    Embedding runs on CPU at roughly three 400-token chunks per second, so cost
    scales with chunk count, not file size. A 17 MB Word report of mostly photos
    produces ~600 chunks and indexes in a couple of minutes; a 5 MB spreadsheet
    of 120,000 transaction rows produces ~10,800 and would take about an hour,
    during which the upload looks like it has hung.

    Beyond the cost, indexing ten thousand near-identical transaction rows is
    actively bad for retrieval: they crowd out the genuine documents in every
    search, and no user was ever going to ask about row 45,231 by name.

    Truncation is spread proportionally across pages or sheets rather than
    taking the first N chunks, so that every sheet of a workbook stays
    represented instead of the last dozen vanishing entirely. The caller
    surfaces the returned summary to whoever uploaded the file — a silently
    half-indexed document is worse than a slow one.
    """
    if len(chunks) <= budget:
        return chunks, None

    produced = len(chunks)

    # Preserve document order while grouping by page (sheet index for Excel).
    groups: dict[int, list[TextChunk]] = {}
    for chunk in chunks:
        groups.setdefault(chunk.page_number, []).append(chunk)

    per_group = max(1, budget // len(groups))
    kept: list[TextChunk] = []
    for page in sorted(groups):
        kept.extend(groups[page][:per_group])

    # Spend any leftover budget on the earliest pages, which tend to carry
    # headers, totals and summaries.
    if len(kept) < budget:
        kept_ids = {id(c) for c in kept}
        for page in sorted(groups):
            for chunk in groups[page]:
                if len(kept) >= budget:
                    break
                if id(chunk) not in kept_ids:
                    kept.append(chunk)
                    kept_ids.add(id(chunk))
            if len(kept) >= budget:
                break

    kept.sort(key=lambda c: (c.page_number, c.chunk_index))
    return kept, {
        "chunks_produced": produced,
        "chunks_indexed": len(kept),
        "pages_or_sheets": len(groups),
        "message": (
            f"This file produced {produced:,} sections, more than the {budget:,} "
            f"indexed per document. The first {per_group} section(s) of each of its "
            f"{len(groups)} page(s)/sheet(s) were indexed so every part stays "
            "searchable. For very large transactional spreadsheets, uploading a "
            "summary sheet gives better answers than the full row-level export."
        ),
    }


def _mime_for_suffix(suffix: str) -> str:
    _map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".log": "text/plain",
    }
    return _map.get(suffix, "application/octet-stream")
