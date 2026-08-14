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
import re
from pathlib import Path
from typing import Optional

import structlog
from rank_bm25 import BM25Okapi
from sqlalchemy import func, select
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

# Every distinct token in the active corpus. This is the single source of truth
# for "words the documents actually contain", used by typo correction to decide
# what a misspelling could plausibly have meant. Kept here rather than in a
# second structure elsewhere so it can never drift from the index it describes.
_vocabulary: frozenset[str] = frozenset()

# Bumped on every rebuild or reload. Consumers that derive their own structures
# from the vocabulary (phonetic keys, length buckets) key their caches on this,
# so an ingestion invalidates them automatically instead of serving corrections
# against the previous document set.
_vocabulary_epoch: int = 0


# Bumped whenever _tokenize changes. A persisted index tokenized by an older
# rule cannot be queried with a newer one — the tokens simply won't line up —
# so a version mismatch forces a rebuild instead of silently degrading recall.
_TOKENIZER_VERSION = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """
    Lowercase, punctuation-stripped tokenizer for BM25.

    Splitting on whitespace alone kept punctuation attached to the token, so a
    query for "MinIO?" produced the token "minio?" and matched nothing in an
    index built from "minio". Keyword search then silently dropped out of the
    hybrid merge for any question ending in a question mark — which is most of
    them.
    """
    return _TOKEN_RE.findall(text.lower())


async def build_index(db: AsyncSession) -> None:
    """
    Build (or rebuild) the BM25 index from all active chunks in Postgres.

    Should be called:
      - Once at application startup
      - After each new document ingestion
    """
    global _bm25, _chunk_ids, _chunk_texts, _vocabulary, _vocabulary_epoch

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
        _vocabulary = frozenset()
        _vocabulary_epoch += 1
        return

    _chunk_ids = [str(row.qdrant_point_id) for row in rows]
    _chunk_texts = [row.text for row in rows]
    tokenized = [_tokenize(text) for text in _chunk_texts]

    _bm25 = BM25Okapi(tokenized)
    _vocabulary = frozenset(token for tokens in tokenized for token in tokens)
    _vocabulary_epoch += 1
    logger.info(
        "bm25.build.complete",
        corpus_size=len(_chunk_ids),
        vocabulary_size=len(_vocabulary),
    )

    # Persist to disk for faster restarts
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_INDEX_PATH, "wb") as f:
        pickle.dump(
            {
                "chunk_ids": _chunk_ids,
                "chunk_texts": _chunk_texts,
                "bm25": _bm25,
                "tokenizer_version": _TOKENIZER_VERSION,
                "vocabulary": _vocabulary,
            },
            f,
        )
    logger.info("bm25.saved", path=str(_INDEX_PATH))


def load_index_from_disk() -> bool:
    """
    Attempt to load the BM25 index from the persisted pickle file.

    Returns True if successfully loaded, False if not found or corrupt.
    """
    global _bm25, _chunk_ids, _chunk_texts, _vocabulary, _vocabulary_epoch

    if not _INDEX_PATH.exists():
        return False

    try:
        with open(_INDEX_PATH, "rb") as f:
            data = pickle.load(f)

        if data.get("tokenizer_version") != _TOKENIZER_VERSION:
            logger.warning(
                "bm25.tokenizer_version_mismatch",
                index_version=data.get("tokenizer_version"),
                current_version=_TOKENIZER_VERSION,
            )
            return False

        _bm25 = data["bm25"]
        _chunk_ids = data["chunk_ids"]
        _chunk_texts = data["chunk_texts"]

        # Indexes written before the vocabulary was persisted are still valid —
        # recover it from the chunk texts rather than forcing a full rebuild.
        stored_vocabulary = data.get("vocabulary")
        if stored_vocabulary is None:
            _vocabulary = frozenset(
                token for text in _chunk_texts for token in _tokenize(text)
            )
            logger.info("bm25.vocabulary_derived", vocabulary_size=len(_vocabulary))
        else:
            _vocabulary = frozenset(stored_vocabulary)

        _vocabulary_epoch += 1
        logger.info(
            "bm25.loaded_from_disk",
            corpus_size=len(_chunk_ids),
            vocabulary_size=len(_vocabulary),
        )
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


def get_vocabulary() -> frozenset[str]:
    """
    Every distinct token in the currently active corpus.

    This follows the documents: ingest a new file and the next rebuild picks up
    its words automatically. Nothing downstream should ever keep its own list of
    known terms — that is how a system ends up only understanding the documents
    it was developed against.
    """
    return _vocabulary


def get_vocabulary_epoch() -> int:
    """
    Counter identifying the current vocabulary. Changes on every rebuild.

    Consumers that precompute structures over the vocabulary should cache
    against this value so an ingestion invalidates them.
    """
    return _vocabulary_epoch


async def count_active_chunks(db: AsyncSession) -> int:
    """
    Count the chunks belonging to active document versions.

    Used at startup to detect a stale on-disk index: if this disagrees with
    get_index_size(), the pickle predates an ingestion and must be rebuilt.
    """
    result = await db.execute(
        select(func.count(Chunk.id))
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(DocumentVersion.is_active == True)  # noqa: E712
    )
    return int(result.scalar_one())
