"""
ingestion/bm25_index.py
────────────────────────
BM25 sparse keyword index using rank-bm25.

The BM25 index is built in-memory at startup from all active chunk
texts stored in PostgreSQL, and rebuilt incrementally after each new
document ingestion.

Design notes:
  - The index is stored as a pickled file (bm25_index.pkl) that is
    reloaded at startup to avoid re-building from scratch.
  - Index rebuild is always triggered after a new document version is
    ingested to keep BM25 in sync with the active Qdrant corpus.
  - Access control (PEP) cannot be applied at BM25 query time the way
    it can in Qdrant; instead, BM25 candidates are post-filtered against
    the Qdrant-filtered dense results via RRF to ensure only permitted
    chunks reach the reranker.
"""

from __future__ import annotations

import asyncio
import os
import pickle
from pathlib import Path
from typing import Optional

import structlog
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.models import Chunk, DocumentVersion

logger = structlog.get_logger(__name__)
settings = get_settings()

_INDEX_PATH = Path("data/bm25_index.pkl")

# In-memory index state
_bm25: Optional[BM25Okapi] = None
_chunk_ids: list[str] = []      # Parallel list: _chunk_ids[i] → _bm25 corpus[i]
_chunk_texts: list[str] = []


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + lowercase tokenizer for BM25."""
    return text.lower().split()


async def build_index(db: AsyncSession) -> None:
    """
    Build (or rebuild) the BM25 index from all active chunks in Postgres.

    Should be called:
      - Once at application startup
      - After each new document ingestion
    """
    global _bm25, _chunk_ids, _chunk_texts

    # Load all chunks belonging to active document versions
    result = await db.execute(
        select(Chunk.id, Chunk.text, Chunk.qdrant_point_id)
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(DocumentVersion.is_active == True)  # noqa: E712
        .order_by(Chunk.id)
    )
    rows = result.all()

    if not rows:
        logger.warning("bm25.build.empty_corpus")
        _bm25 = None
        _chunk_ids = []
        _chunk_texts = []
        return

    _chunk_ids = [str(row.qdrant_point_id) for row in rows]
    _chunk_texts = [row.text for row in rows]
    tokenized = [_tokenize(text) for text in _chunk_texts]

    _bm25 = BM25Okapi(tokenized)
    logger.info("bm25.build.complete", corpus_size=len(_chunk_ids))

    # Persist to disk for faster restarts
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_INDEX_PATH, "wb") as f:
        pickle.dump({"chunk_ids": _chunk_ids, "chunk_texts": _chunk_texts, "bm25": _bm25}, f)
    logger.info("bm25.saved", path=str(_INDEX_PATH))


def load_index_from_disk() -> bool:
    """
    Attempt to load the BM25 index from the persisted pickle file.

    Returns True if successfully loaded, False if not found or corrupt.
    """
    global _bm25, _chunk_ids, _chunk_texts

    if not _INDEX_PATH.exists():
        return False

    try:
        with open(_INDEX_PATH, "rb") as f:
            data = pickle.load(f)
        _bm25 = data["bm25"]
        _chunk_ids = data["chunk_ids"]
        _chunk_texts = data["chunk_texts"]
        logger.info("bm25.loaded_from_disk", corpus_size=len(_chunk_ids))
        return True
    except Exception as e:
        logger.error("bm25.load_failed", error=str(e))
        return False


def search(query: str, top_k: int = 20) -> list[dict]:
    """
    Search the BM25 index.

    Returns:
        List of dicts with 'qdrant_point_id' and 'bm25_score',
        sorted descending by score, top_k items.
    """
    if _bm25 is None or not _chunk_ids:
        logger.warning("bm25.search.index_not_ready")
        return []

    tokenized_query = _tokenize(query)
    scores = _bm25.get_scores(tokenized_query)

    # Pair scores with chunk IDs and sort
    scored = sorted(
        zip(_chunk_ids, scores), key=lambda x: x[1], reverse=True
    )

    results = []
    for qdrant_point_id, score in scored[:top_k]:
        if score > 0:
            results.append({"qdrant_point_id": qdrant_point_id, "bm25_score": float(score)})

    return results


def get_index_size() -> int:
    """Return the number of documents in the current BM25 index."""
    return len(_chunk_ids)
