"""
config/settings.py
──────────────────
Single source of truth for all configuration.
Reads from environment variables / .env file via Pydantic BaseSettings.

CRITICAL: No values in this file should ever be hardcoded for production use.
Switching from Ollama (dev) to vLLM (prod), or migrating to new hardware,
requires ONLY changes to the .env file — never application code changes.
"""

from functools import lru_cache
from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: str = "change_this_to_a_random_secret_at_least_32_chars"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── Service authentication (trusted gateway) ─────────────────
    # Open WebUI authenticates the user itself, then asks this API for a session
    # on their behalf, presenting this secret instead of the user's password.
    # That is what lets users change their own passwords without the chat
    # pipeline knowing them — or breaking when they do.
    #
    # This secret can mint a session for ANY user, so it is env-only and never
    # defaulted to a usable value: an empty string disables issuance entirely
    # (fail closed) rather than leaving a guessable shared secret in place.
    service_token: str = ""

    # Minimum length enforced on any password set through the API.
    min_password_length: int = 8

    # ── PostgreSQL ───────────────────────────────────────────────
    postgres_user: str = "sparkline"
    postgres_password: str = "sparkline_secret"
    postgres_db: str = "sparkline_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def database_url_sync(self) -> str:
        """Used by Alembic migrations (psycopg2-based)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis (Document RAG only — not shared with enterprise adapters) ──
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = "redis_secret"
    redis_db: int = 0
    redis_session_ttl_seconds: int = 3600

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── MinIO ────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minio_secret"
    minio_bucket_documents: str = "sparkline-documents"
    minio_secure: bool = False

    # ── Qdrant ───────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "sparkline_documents"
    # Per-chat attachments live in their own collection, never in the corpus
    # one. That separation is the isolation mechanism: a corpus query cannot
    # return a session chunk even if its filter is wrong, because it is not
    # searching the collection those chunks are in.
    qdrant_session_collection_name: str = "sparkline_session_docs"
    qdrant_vector_size: int = 1024  # BAAI/bge-large-en output dim
    qdrant_api_key: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    # ── LLM Serving — OpenAI-compatible endpoint ─────────────────
    # To swap Ollama → vLLM: change only these three env vars.
    # Application code never references the backend directly.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model_name: str = "qwen2.5:14b"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 120

    # ── Embeddings ───────────────────────────────────────────────
    embedding_model_name: str = "BAAI/bge-large-en"
    embedding_device: str = "cpu"  # Set to 'cuda' once GPU is available

    # ── Reranker ─────────────────────────────────────────────────
    reranker_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_device: str = "cpu"  # Set to 'cuda' once GPU is available

    # ── Retrieval Settings ────────────────────────────────────────
    retrieval_top_k_dense: int = 20
    retrieval_top_k_bm25: int = 20
    # Candidates that survive the RRF merge and are handed to the cross-encoder.
    # Must be larger than retrieval_top_k_rerank, otherwise the fusion step
    # truncates to the final answer size and the reranker can only reorder what
    # RRF already chose — it can never promote a chunk RRF ranked too low.
    retrieval_top_k_fusion: int = 20
    retrieval_top_k_rerank: int = 5
    retrieval_rrf_k: int = 60  # RRF constant — higher = less steep rank decay

    # ── Chunking ─────────────────────────────────────────────────
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 80

    # Ceiling on chunks indexed from a single document. Cost is driven by chunk
    # count, not file size: embedding runs on CPU at roughly three chunks per
    # second, so 1,000 chunks is about five minutes. A 120,000-row spreadsheet
    # produces ~10,800 chunks — close to an hour, during which the upload looks
    # frozen — and floods retrieval with near-identical rows. Beyond the ceiling
    # the document is indexed proportionally across its pages/sheets and the
    # uploader is told, rather than the file being rejected or silently halved.
    # Raise it if a slow upload is acceptable; embedding on GPU would lift this
    # substantially, but that needs the api container rebuilt with GPU access.
    ingest_max_chunks_per_document: int = 1000

    # ── Query Routing ─────────────────────────────────────────────
    # 'evidence' routes on retrieval quality: run retrieval, then read the top
    # cross-encoder rerank score to choose documents vs. general knowledge.
    # 'legacy' restores the original always-RAG behaviour for a fast rollback.
    router_mode: Literal["evidence", "legacy"] = "evidence"

    # Rerank-score bands. The cross-encoder emits raw logits (~-11..+11), not
    # probabilities, so these are corpus-specific and MUST be re-measured with
    # `python -m eval.calibrate_router` whenever the document set changes
    # substantially. Current values come from a 12-in-corpus / 15-general run
    # against the 93-chunk pilot corpus (2026-08-11).
    #   score >= high        → documents only, strict grounded prompt
    #   low <= score < high  → blended: documents supplied, general knowledge
    #                          permitted, model must flag which is which
    #   score < low          → general knowledge only, no context, no citations
    router_rag_score_high: float = -2.0
    router_rag_score_low: float = -5.5

    # If a strict-RAG answer still comes back as "I couldn't find this in the
    # documents", retry the query in general mode rather than showing the user
    # a dead end. Does not apply when the user explicitly named a document.
    router_enable_general_fallback: bool = True

    # Prepend the previous question to the retrieval query for short/anaphoric
    # follow-ups ("what about last year?") so they don't drop out of a document
    # conversation. Affects retrieval only, never the prompt.
    router_condense_followups: bool = True

    # ── Typo Tolerance ────────────────────────────────────────────
    # A misspelled document question retrieves worse, scores lower, and falls
    # out of the document band into general knowledge — so typo handling is part
    # of routing, not a cosmetic nicety.
    #
    # Corrections are drawn from the vocabulary of whatever documents are
    # currently ingested (see ingestion/bm25_index.get_vocabulary), which is
    # rebuilt on every ingestion. Nothing here is specific to any document:
    # upload a new corpus and the vocabulary follows it with no code change.
    typo_correction_enabled: bool = True

    # Tokens shorter than this are never corrected. Short words are mostly
    # function words, and at 3 characters almost everything is within edit
    # distance 1 of something.
    typo_min_token_length: int = 4

    # Damerau-Levenshtein budget. Applied as: distance 1 for tokens of
    # 4-6 characters, up to this value for 7+. Transpositions count as one edit
    # ("waht" → "what"), which plain Levenshtein would score as two.
    typo_max_edit_distance: int = 2

    # Second pass for misspellings that are too far off for edit distance but
    # sound right ("diprisiation" → "depreciation"). Costs no GPU.
    typo_phonetic_enabled: bool = True

    # Never "correct" an ordinary English word just because the documents happen
    # not to use it. A word missing from the corpus is not evidence of a typo —
    # with a small corpus it is usually evidence of a general question. Without
    # this, "tell me a joke" was searched for as "well me a joke".
    #
    # The word list is the reranker's own tokenizer vocabulary (~30k English
    # word pieces), which is already loaded for scoring. No extra dependency, no
    # hand-maintained dictionary, and it is corpus-independent by construction.
    typo_protect_dictionary_words: bool = True

    # Tier 3: ask the LLM to rewrite a query that still finds nothing after the
    # cheap passes. Genuinely semantic, but non-deterministic, adds a GPU
    # round-trip, and will happily "correct" domain terms like BOQ or Field
    # Circle. Off by default — turn on only if real usage shows the cheap
    # passes are not enough. Never fires unless the query has words the corpus
    # does not contain AND retrieval already failed.
    typo_semantic_rewrite_enabled: bool = False
    typo_semantic_rewrite_timeout_seconds: int = 15

    # ── Access Control ────────────────────────────────────────────
    # Default role for pilot users who have no dept/designation yet.
    # This is a temporary stand-in; real per-user restriction activates
    # as soon as dept/designation attributes are supplied.
    default_pilot_role: str = "pilot_user"

    # ── API Gateway ───────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @field_validator("app_secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters long")
        return v


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton. Import and call this everywhere."""
    return Settings()
