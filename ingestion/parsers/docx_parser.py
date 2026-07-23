"""
ingestion/parsers/docx_parser.py
──────────────────────────────────
Word document (.docx) parser using python-docx.

Extracts:
  - Paragraph text (with heading level preserved as metadata)
  - Table content (cells joined with tab separators per row)
  - Text boxes (via XML traversal — python-docx doesn't expose these natively)

Returns a flat list of PageContent-compatible dicts since .docx has no
page numbers — we use logical section numbers instead.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import structlog
from docx import Document
from docx.oxml.ns import qn

logger = structlog.get_logger(__name__)


@dataclass
class DocxBlock:
    """A logical block of text from a .docx document."""

    block_index: int          # 0-based position in document
    text: str
    block_type: str           # 'paragraph' | 'table' | 'textbox' | 'heading'
    heading_level: Optional[int] = None   # 1–9 for headings, None otherwise


def parse_docx(file_bytes: bytes, filename: str = "document.docx") -> list[DocxBlock]:
    """
    Parse a .docx file from raw bytes.

    Returns an ordered list of DocxBlock objects representing all
    text content in document order.
    """
    try:
        doc = Document(io.BytesIO(file_bytes))
    except Exception as e:
        raise ValueError(f"Failed to open .docx file '{filename}': {e}") from e

    blocks: list[DocxBlock] = []
    idx = 0

    # ── Paragraphs and Headings ──────────────────────────────────
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # Detect heading level from style name (e.g., 'Heading 1' → level 1)
        style_name = para.style.name if para.style else ""
        heading_level = None
        block_type = "paragraph"
        if style_name.startswith("Heading"):
            try:
                heading_level = int(style_name.split()[-1])
                block_type = "heading"
            except (ValueError, IndexError):
                pass

        blocks.append(
            DocxBlock(
                block_index=idx,
                text=text,
                block_type=block_type,
                heading_level=heading_level,
            )
        )
        idx += 1

    # ── Tables ────────────────────────────────────────────────────
    for table in doc.tables:
        table_lines: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            row_text = "\t".join(cells)
            if row_text.strip():
                table_lines.append(row_text)

        if table_lines:
            blocks.append(
                DocxBlock(
                    block_index=idx,
                    text="\n".join(table_lines),
                    block_type="table",
                )
            )
            idx += 1

    # ── Text Boxes (XML traversal) ────────────────────────────────
    for shape in doc.element.body.iter(qn("w:txbxContent")):
        texts = [
            t.text
            for t in shape.iter(qn("w:t"))
            if t.text and t.text.strip()
        ]
        joined = " ".join(texts).strip()
        if joined:
            blocks.append(
                DocxBlock(
                    block_index=idx,
                    text=joined,
                    block_type="textbox",
                )
            )
            idx += 1

    logger.info(
        "docx_parser.complete",
        filename=filename,
        total_blocks=len(blocks),
        headings=sum(1 for b in blocks if b.block_type == "heading"),
        tables=sum(1 for b in blocks if b.block_type == "table"),
    )
    return blocks
