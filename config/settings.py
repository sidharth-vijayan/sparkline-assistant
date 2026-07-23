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
    llm_model_name: str = "hf.co/mradermacher/GPT-OSS-20B-i1-GGUF:Q4_K_M"
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
    retrieval_top_k_rerank: int = 5
    retrieval_rrf_k: int = 60  # RRF constant — higher = less steep rank decay

    # ── Chunking ─────────────────────────────────────────────────
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 80

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
