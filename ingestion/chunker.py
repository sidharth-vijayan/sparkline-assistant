"""
ingestion/chunker.py
─────────────────────
Chunking logic for all document types.

CRITICAL: Chunking runs ONCE at ingestion time. Queries never re-chunk.

Strategy:
  - Token-aware chunking using tiktoken (cl100k_base tokenizer — compatible
    with most embedding models including bge-large-en)
  - Sliding window with configurable size + overlap
  - Page boundaries are respected: a chunk never spans more than one page
    (this ensures page_number citations are always accurate)
  - Very short pages (fewer tokens than MIN_CHUNK_TOKENS) are merged with the
    next page before chunking to avoid single-sentence chunks that rank poorly

Returns a list of Chunk dataclasses ready for embedding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import tiktoken
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Use cl100k_base for token counting (GPT-3.5/4 standard — broadly compatible)
_TOKENIZER = tiktoken.get_encoding("cl100k_base")

MIN_CHUNK_TOKENS = 50  # Pages with fewer tokens are merged with the next


@dataclass
class TextChunk:
    chunk_index: int       # Global index across all chunks for this document
    text: str
    page_number: Optional[int]   # 1-indexed; None for non-paged formats (Excel, docx)
    token_count: int
    source_block_indices: list[int]   # Which input blocks/pages this came from


def chunk_text(
    text: str,
    page_number: Optional[int] = None,
    start_index: int = 0,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> list[TextChunk]:
    """
    Chunk a single text string (from one page or one docx block).

    Args:
        text: The text to chunk
        page_number: Page number to attach to all resulting chunks
        start_index: Starting chunk_index offset (for multi-page accumulation)
        chunk_size: Override settings.chunk_size_tokens
        chunk_overlap: Override settings.chunk_overlap_tokens

    Returns a list of TextChunk objects.
    """
    size = chunk_size or settings.chunk_size_tokens
    overlap = chunk_overlap or settings.chunk_overlap_tokens

    tokens = _TOKENIZER.encode(text)
    if not tokens:
        return []

    chunks: list[TextChunk] = []
    pos = 0
    chunk_idx = start_index

    while pos < len(tokens):
        end = min(pos + size, len(tokens))
        chunk_tokens = tokens[pos:end]
        chunk_text_str = _TOKENIZER.decode(chunk_tokens)

        if chunk_text_str.strip():
            chunks.append(
                TextChunk(
                    chunk_index=chunk_idx,
                    text=chunk_text_str,
                    page_number=page_number,
                    token_count=len(chunk_tokens),
                    source_block_indices=[page_number] if page_number else [],
                )
            )
            chunk_idx += 1

        if end >= len(tokens):
            break
        pos += size - overlap

    return chunks


def chunk_pdf_pages(pages: list) -> list[TextChunk]:
    """
    Chunk a list of PageContent objects from the PDF parser.

    Short pages (< MIN_CHUNK_TOKENS) are merged with the following page
    before chunking to avoid creating trivially small chunks.
    """
    all_chunks: list[TextChunk] = []
    chunk_idx = 0
    pending_text = ""
    pending_pages: list[int] = []

    for page in pages:
        text = page.text.strip()
        if not text:
            continue

        tokens = _TOKENIZER.encode(text)

        if len(tokens) < MIN_CHUNK_TOKENS:
            # Merge with next page's text
            pending_text += f"\n{text}"
            pending_pages.append(page.page_number)
            continue

        # Flush any pending short content as a prefix to this page
        full_text = (pending_text + "\n" + text).strip() if pending_text else text
        pending_text = ""
        pending_pages = []

        new_chunks = chunk_text(
            text=full_text,
            page_number=page.page_number,
            start_index=chunk_idx,
        )
        all_chunks.extend(new_chunks)
        chunk_idx += len(new_chunks)

    # Flush remaining pending text
    if pending_text.strip():
        new_chunks = chunk_text(
            text=pending_text.strip(),
            page_number=pending_pages[0] if pending_pages else None,
            start_index=chunk_idx,
        )
        all_chunks.extend(new_chunks)

    logger.info(
        "chunker.pdf_complete",
        total_pages=len(pages),
        total_chunks=len(all_chunks),
        avg_tokens=round(
            sum(c.token_count for c in all_chunks) / len(all_chunks), 1
        ) if all_chunks else 0,
    )
    return all_chunks


def chunk_docx_blocks(blocks: list) -> list[TextChunk]:
    """
    Chunk a list of DocxBlock objects from the DOCX parser.

    Docx has no page numbers, so page_number is None.
    Each block is chunked independently.
    """
    all_chunks: list[TextChunk] = []
    chunk_idx = 0

    for block in blocks:
        new_chunks = chunk_text(
            text=block.text,
            page_number=None,
            start_index=chunk_idx,
        )
        all_chunks.extend(new_chunks)
        chunk_idx += len(new_chunks)

    logger.info("chunker.docx_complete", total_blocks=len(blocks), total_chunks=len(all_chunks))
    return all_chunks


def chunk_excel_sheets(sheets: list) -> list[TextChunk]:
    """
    Chunk a list of SheetContent objects from the Excel parser.

    Each sheet's text representation is chunked independently.
    Page number is set to the sheet index (1-based) for citation purposes.
    """
    all_chunks: list[TextChunk] = []
    chunk_idx = 0

    for sheet_idx, sheet in enumerate(sheets, start=1):
        text = sheet.to_text()
        new_chunks = chunk_text(
            text=text,
            page_number=sheet_idx,  # Use sheet index as "page"
            start_index=chunk_idx,
        )
        all_chunks.extend(new_chunks)
        chunk_idx += len(new_chunks)

    logger.info("chunker.excel_complete", total_sheets=len(sheets), total_chunks=len(all_chunks))
    return all_chunks
