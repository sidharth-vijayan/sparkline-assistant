"""
ingestion/parsers/ocr_parser.py
─────────────────────────────────
Tesseract OCR integration for scanned PDF pages and image files.

This is called by the ingestion pipeline for pages flagged as
'ocr_needed' by the PDF parser. It renders the page to an image
then passes it through Tesseract.

Requirements:
  - Tesseract must be installed on the host system.
  - Windows: https://github.com/UB-Mannheim/tesseract/wiki
  - Linux:   apt-get install tesseract-ocr
  - Set TESSERACT_CMD in .env if not in system PATH.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytesseract
import structlog
from PIL import Image

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Allow overriding the Tesseract binary path via environment
_TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

# Tesseract config: OEM 3 (default LSTM), PSM 3 (fully automatic page segmentation)
_TESS_CONFIG = "--oem 3 --psm 3"


@dataclass
class OcrResult:
    page_number: int
    text: str
    confidence: float  # 0.0–100.0 average confidence


def ocr_image_bytes(image_bytes: bytes, page_number: int = 1) -> OcrResult:
    """
    Run Tesseract OCR on raw PNG/JPEG image bytes.

    Args:
        image_bytes: Raw image data (PNG or JPEG)
        page_number: Page number (for logging/citation purposes)

    Returns:
        OcrResult with extracted text and confidence score
    """
    try:
        from io import BytesIO
        image = Image.open(BytesIO(image_bytes)).convert("RGB")

        # Get text + confidence data
        data = pytesseract.image_to_data(
            image,
            config=_TESS_CONFIG,
            output_type=pytesseract.Output.DICT,
        )

        # Filter words with confidence > 0
        words = []
        confidences = []
        for i, word in enumerate(data["text"]):
            conf = int(data["conf"][i])
            if conf > 0 and word.strip():
                words.append(word)
                confidences.append(conf)

        text = " ".join(words)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        logger.info(
            "ocr.complete",
            page_number=page_number,
            words_extracted=len(words),
            avg_confidence=round(avg_conf, 1),
        )
        return OcrResult(page_number=page_number, text=text, confidence=avg_conf)

    except Exception as e:
        logger.error("ocr.failed", page_number=page_number, error=str(e))
        return OcrResult(page_number=page_number, text="", confidence=0.0)


def ocr_pdf_page(pdf_bytes: bytes, page_index: int, dpi: int = 200) -> OcrResult:
    """
    Render a PDF page to an image and run Tesseract on it.

    Args:
        pdf_bytes: Raw PDF file bytes
        page_index: 0-based page index
        dpi: Render resolution (higher = better OCR, slower)

    Returns:
        OcrResult for the page
    """
    from ingestion.parsers.pdf_parser import render_page_as_image

    try:
        image_bytes = render_page_as_image(pdf_bytes, page_index, dpi=dpi)
        return ocr_image_bytes(image_bytes, page_number=page_index + 1)
    except Exception as e:
        logger.error("ocr.pdf_page_failed", page_index=page_index, error=str(e))
        return OcrResult(page_number=page_index + 1, text="", confidence=0.0)
