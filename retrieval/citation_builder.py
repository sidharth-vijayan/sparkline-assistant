"""
retrieval/citation_builder.py
───────────────────────────────
Build per-answer citation objects from reranked chunk payloads.

Citation schema (also the interface contract for Dhruv's enterprise adapters
if they ever need to reference policy documents via the same engine):

{
  "document_name": str,        # Original filename
  "page_number": int | None,   # 1-indexed; null for non-paged formats
  "version_uploaded_at": str,  # ISO 8601 UTC timestamp
  "chunk_text_preview": str,   # First 200 chars of the chunk text
  "rerank_score": float        # Cross-encoder confidence
}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from retrieval.prompt_defence import fence_passage


@dataclass
class Citation:
    document_name: str
    page_number: Optional[int]
    version_uploaded_at: str
    chunk_text_preview: str
    rerank_score: float

    def to_dict(self) -> dict:
        return asdict(self)

    def format_inline(self) -> str:
        """Human-readable inline citation string for injection into LLM prompts."""
        page_str = f"p.{self.page_number}" if self.page_number else "n/a"
        return f"[{self.document_name}, {page_str}, uploaded {self.version_uploaded_at[:10]}]"


def build_citations(reranked_chunks: list[dict]) -> list[Citation]:
    """
    Build Citation objects from a list of reranked chunk payload dicts.

    Args:
        reranked_chunks: Output from retrieval.reranker.rerank()
            Each dict must have Qdrant payload fields:
              - document_name
              - page_number (may be None)
              - uploaded_at
              - text
              - rerank_score

    Returns:
        List of Citation objects, in the same order as the input.
    """
    citations: list[Citation] = []

    for chunk in reranked_chunks:
        payload = chunk  # Qdrant payload is merged directly into the chunk dict
        text_preview = (payload.get("text") or "")[:200].strip()
        if len(payload.get("text", "")) > 200:
            text_preview += "…"

        citations.append(
            Citation(
                document_name=payload.get("document_name", "Unknown"),
                page_number=payload.get("page_number"),
                version_uploaded_at=payload.get("uploaded_at", ""),
                chunk_text_preview=text_preview,
                rerank_score=float(payload.get("rerank_score", 0.0)),
            )
        )

    return citations


def build_context_block(
    reranked_chunks: list[dict],
    citations: list[Citation],
) -> str:
    """
    Build the context string injected into the LLM prompt.

    Format:
        [SOURCE 1] Document: Q3_Report.pdf | Page: 7 | Uploaded: 2025-07-15
        <chunk text>

        [SOURCE 2] ...
    """
    lines: list[str] = []

    for i, (chunk, citation) in enumerate(zip(reranked_chunks, citations), start=1):
        page_str = f"Page: {citation.page_number}" if citation.page_number else "Page: N/A"
        header = (
            f"[SOURCE {i}] Document: {citation.document_name} | "
            f"{page_str} | Uploaded: {citation.version_uploaded_at[:10]}"
        )
        lines.append(header)
        # The passage is wrapped in an explicit data region. Retrieved text is
        # untrusted — a document whose contents contain instructions was shown
        # to hijack the answer — so the boundary has to be visible to the model
        # rather than implied by layout.
        lines.append(fence_passage(chunk.get("text", "")))
        lines.append("")  # blank separator

    return "\n".join(lines).strip()
