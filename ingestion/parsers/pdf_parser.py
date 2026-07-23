"""
ingestion/parsers/pdf_parser.py
────────────────────────────────
PDF parser using pdfplumber as the primary engine.
Falls back to PyMuPDF (fitz) for pages that pdfplumber can't read.
Tesseract OCR is invoked for pages with no extractable text
(i.e., scanned/image-based PDFs). See ocr_parser.py for OCR details.

Returns a list of PageContent objects (page number + text).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
import pdfplumber
import structlog

logger = structlog.get_logger(__name__)

# Minimum characters per page before we assume it's a scanned image
MIN_CHARS_FOR_TEXT_PAGE = 30


@dataclass
class PageContent:
    page_number: int  # 1-indexed
    text: str
    extraction_method: str  # 'pdfplumber' | 'pymupdf' | 'ocr'


def parse_pdf(file_bytes: bytes, filename: str = "document.pdf") -> list[PageContent]:
    """
    Parse a PDF from raw bytes.

    Strategy per page:
    1. Try pdfplumber — best for native text PDFs with layout preservation
    2. Fall back to PyMuPDF — faster, handles more edge cases
    3. If extracted text is too short → flag for OCR (handled in pipeline.py)

    Returns a list of PageContent, one per page.
    """
    pages: list[PageContent] = []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    text = text.strip()

                    if len(text) >= MIN_CHARS_FOR_TEXT_PAGE:
                        pages.append(
                            PageContent(
                                page_number=page_num,
                                text=text,
                                extraction_method="pdfplumber",
                            )
                        )
                    else:
                        # Not enough text — try PyMuPDF fallback
                        fitz_text = _extract_page_pymupdf(file_bytes, page_num - 1)
                        if fitz_text and len(fitz_text) >= MIN_CHARS_FOR_TEXT_PAGE:
                            pages.append(
                                PageContent(
                                    page_number=page_num,
                                    text=fitz_text,
                                    extraction_method="pymupdf",
                                )
                            )
                        else:
                            # Mark for OCR — will be filled in by pipeline.py
                            pages.append(
                                PageContent(
                                    page_number=page_num,
                                    text="",
                                    extraction_method="ocr_needed",
                                )
                            )
                except Exception as page_error:
                    logger.warning(
                        "pdf_parser.page_error",
                        filename=filename,
                        page=page_num,
                        error=str(page_error),
                    )
                    pages.append(
                        PageContent(
                            page_number=page_num,
                            text="",
                            extraction_method="ocr_needed",
                        )
                    )

    except Exception as e:
        logger.error("pdf_parser.failed", filename=filename, error=str(e))
        raise ValueError(f"Failed to parse PDF '{filename}': {e}") from e

    logger.info(
        "pdf_parser.complete",
        filename=filename,
        total_pages=len(pages),
        needs_ocr=sum(1 for p in pages if p.extraction_method == "ocr_needed"),
    )
    return pages


def _extract_page_pymupdf(file_bytes: bytes, page_index: int) -> Optional[str]:
    """Extract text from a single page using PyMuPDF."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc[page_index]
        text = page.get_text("text")
        doc.close()
        return text.strip() if text else None
    except Exception as e:
        logger.debug("pymupdf.page_fallback_failed", page_index=page_index, error=str(e))
        return None


def render_page_as_image(file_bytes: bytes, page_index: int, dpi: int = 200) -> bytes:
    """
    Render a single PDF page as a PNG image (bytes).
    Used by the OCR pipeline when text extraction fails.
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page = doc[page_index]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()
    return img_bytes
