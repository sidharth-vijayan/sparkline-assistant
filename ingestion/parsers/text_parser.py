"""
ingestion/parsers/text_parser.py
──────────────────────────────────
Plain-text and CSV parser.

These formats carry no structure of their own, so there is nothing to extract —
the work is entirely in reading the bytes correctly and, for CSV, presenting
rows in the same shape the Excel parser produces so that retrieval and citation
behave identically whether a table arrived as a spreadsheet or an export.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Rows beyond this are dropped, with a note, matching the Excel parser's cap.
MAX_ROWS = 5000

# Tried in order. utf-8-sig strips the byte-order mark Excel writes on every CSV
# it exports, which would otherwise attach itself to the first column heading
# and make that heading unmatchable.
_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1")


@dataclass
class TextDocument:
    text: str
    encoding: str
    row_count: int = 0


def _decode(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """
    Decode bytes to text, trying the encodings business files actually use.

    latin-1 is last and always succeeds — every byte sequence is valid in it. It
    can mangle accented characters, but a slightly wrong character is a far
    better outcome than refusing a document outright.
    """
    for encoding in _ENCODINGS:
        try:
            return file_bytes.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("latin-1", errors="replace"), "latin-1"


def parse_text(file_bytes: bytes, filename: str = "document.txt") -> TextDocument:
    """Read a plain-text file."""
    text, encoding = _decode(file_bytes, filename)
    logger.info(
        "text_parser.complete", filename=filename, encoding=encoding, chars=len(text)
    )
    return TextDocument(text=text.strip(), encoding=encoding)


def parse_csv(file_bytes: bytes, filename: str = "document.csv") -> TextDocument:
    """
    Read a CSV (or tab-separated) file into the same tabular text shape the
    Excel parser emits, so a table reads the same way whichever form it arrived
    in — header row first, then tab-separated values.

    The delimiter is sniffed rather than assumed: exports from Indian and
    European systems are frequently semicolon-separated, and reading one of
    those as comma-separated yields a single meaningless column per row.
    """
    raw, encoding = _decode(file_bytes, filename)
    if not raw.strip():
        return TextDocument(text="", encoding=encoding)

    sample = raw[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    lines: list[str] = []
    total = 0
    for row in reader:
        total += 1
        if len(lines) >= MAX_ROWS:
            continue
        cells = [(c or "").strip() for c in row]
        if any(cells):
            lines.append("\t".join(cells))

    if total > MAX_ROWS:
        lines.append(f"[Note: {total - MAX_ROWS} additional rows truncated]")

    logger.info(
        "csv_parser.complete",
        filename=filename,
        encoding=encoding,
        delimiter=delimiter,
        rows=total,
    )
    return TextDocument(text="\n".join(lines), encoding=encoding, row_count=total)
