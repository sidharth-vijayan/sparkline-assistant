"""
config/settings.py
──────────────────
Single source of truth for all configuration.
Reads from environment variables / .env file via Pydantic BaseSettings.

CRITICAL: No values in this file should ever be hardcoded for production use.
Switching from Ollama (dev) to vLLM (prod), or migrating to new hardware,
requires ONLY changes to the .env file — never application code changes.
"""

import warnings
from functools import lru_cache
from typing import Literal

from pydantic import computed_field, field_validator, model_validator
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
    # Base URL a *browser* can reach this API on, used to build download links
    # for generated files. The pipe talks to the API over the docker network
    # (host.docker.internal), which no browser can resolve, so the link in the
    # chat has to be built from a separately configured public address.
    # Empty means links come out relative, which will not work from a browser.
    public_api_base_url: str = ""

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @field_validator("app_secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters long")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def router_band_is_degenerate(self) -> bool:
        """
        True when the rerank band cannot produce a blended answer.

        router/query_router.py evaluates `blended = score < high` only after
        `score >= low` has already passed, so a high that is not strictly
        greater than low makes that condition unsatisfiable and the blended
        band unreachable. Every query then resolves to documents-only or
        general-only, and no answer marks which parts came from general
        knowledge — a third of the routing behaviour disappears with nothing
        raised anywhere.
        """
        return (
            self.router_mode == "evidence"
            and self.router_rag_score_high <= self.router_rag_score_low
        )

    @model_validator(mode="after")
    def reject_published_credentials(self) -> "Settings":
        """
        Refuse to start on any credential that is published in the repository.

        On 2026-08-19 the live server was found running PostgreSQL, Redis and
        MinIO on the exact placeholder values printed in .env.example — a
        git-tracked file — so anyone who could read the repo held the database
        passwords. Nothing had gone wrong; the placeholders simply worked, so
        nobody was ever prompted to change them.

        This raises rather than warns. A weak password that the operator has
        never seen a message about is indistinguishable from a strong one, and
        unlike the router band there is no measurement to wait for: any random
        value is a correct value, so there is no reason to allow the service to
        run while the fix is pending.
        """
        published = {
            "sparkline_secret",
            "redis_secret",
            "minio_secret",
            "minioadmin",
            "minio_admin",
            "postgres",
            "changeme",
            "change_me",
        }

        offenders: list[str] = []
        for env_name, value in (
            ("POSTGRES_PASSWORD", self.postgres_password),
            ("REDIS_PASSWORD", self.redis_password),
            ("MINIO_ROOT_USER", self.minio_root_user),
            ("MINIO_ROOT_PASSWORD", self.minio_root_password),
        ):
            candidate = (value or "").strip()
            if not candidate:
                offenders.append(f"{env_name} is empty")
            elif candidate.lower() in published:
                offenders.append(f"{env_name} is the placeholder from .env.example")
            elif candidate.upper().startswith("CHANGE_ME"):
                offenders.append(f"{env_name} is still the CHANGE_ME placeholder")

        if offenders:
            raise ValueError(
                "Refusing to start on credentials that are published in the "
                "repository: " + "; ".join(offenders) + ". Generate values with "
                '`python -c "import secrets; print(secrets.token_urlsafe(24))"` '
                "and set them in .env. Changing POSTGRES_PASSWORD also needs "
                "ALTER ROLE inside an existing database — the variable is only "
                "read when the database is first created."
            )
        return self

    @model_validator(mode="after")
    def warn_on_missing_qdrant_api_key(self) -> "Settings":
        """
        Warn — not raise — when Qdrant has no API key.

        Qdrant serves chunk text, including the payload fields the access filter
        is built from, and it authenticates nothing unless this is set. That
        earns a warning on every startup.

        It stops short of raising because, unlike a password, blank is a
        defensible setting: a Qdrant bound to loopback on a developer laptop is
        not exposed by it, and refusing to boot there would push people toward
        pasting a shared key into local .env files. On a shared host, set it.
        """
        if not self.qdrant_api_key.strip():
            warnings.warn(
                "QDRANT_API_KEY is empty: Qdrant will accept any request that "
                "can reach it, and its payload contains full chunk text. Safe "
                "only while the port is bound to loopback. Set a key on any "
                "host other people can reach.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def warn_on_degenerate_router_band(self) -> "Settings":
        """
        Announce a degenerate band loudly instead of raising.

        Raising would be defensible, but it would also refuse to start the
        service until someone supplies numbers, and correct numbers come from
        `python -m eval.calibrate_router` against the real corpus — inventing a
        pair to get the process running is how the band became wrong in the
        first place. So this warns at every startup and
        eval/precommit_checks.py fails on it outright; what it must never do is
        stay quiet.
        """
        if self.router_band_is_degenerate:
            warnings.warn(
                f"ROUTER_RAG_SCORE_HIGH ({self.router_rag_score_high}) is not "
                f"greater than ROUTER_RAG_SCORE_LOW "
                f"({self.router_rag_score_low}): blended answers are impossible "
                f"and no answer will distinguish document content from general "
                f"knowledge. Re-measure with `python -m eval.calibrate_router` "
                f"and set HIGH strictly above LOW.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton. Import and call this everywhere."""
    return Settings()
