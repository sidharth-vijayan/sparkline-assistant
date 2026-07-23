"""
gateway/main.py
────────────────
FastAPI application entry point.

Startup sequence:
  1. Validate settings
  2. Ensure MinIO bucket exists
  3. Ensure Qdrant collection exists
  4. Load BM25 index from disk (or build from Postgres if missing)
  5. Register all routers

All heavy model loading (embedding, reranker) is lazy — first query triggers load.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sparkline AI — LLM + RAG API",
        description=(
            "In-house enterprise LLM + RAG system for Sparkline. "
            "Fully local — no external AI APIs. "
            "OpenAI-compatible /v1/chat/completions endpoint."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────
    # Allows Open WebUI to call this API from the browser
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Restrict to Open WebUI origin in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Startup ───────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        logger.info("sparkline_api.startup", env=settings.app_env)

        # MinIO bucket
        try:
            from services.minio_service import ensure_bucket_exists
            ensure_bucket_exists(settings.minio_bucket_documents)
            logger.info("startup.minio.ok")
        except Exception as e:
            logger.warning("startup.minio.failed", error=str(e))

        # Qdrant collection
        try:
            from services.qdrant_service import ensure_collection_exists
            ensure_collection_exists()
            logger.info("startup.qdrant.ok")
        except Exception as e:
            logger.warning("startup.qdrant.failed", error=str(e))

        # BM25 index — load from disk or build from Postgres
        try:
            from ingestion.bm25_index import build_index, load_index_from_disk
            loaded = load_index_from_disk()
            if not loaded:
                logger.info("startup.bm25.building_from_db")
                from services.postgres_service import AsyncSessionLocal
                async with AsyncSessionLocal() as db:
                    await build_index(db)
            logger.info("startup.bm25.ok")
        except Exception as e:
            logger.warning("startup.bm25.failed", error=str(e))

        logger.info("sparkline_api.ready")

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("sparkline_api.shutdown")

    # ── Routers ───────────────────────────────────────────────────
    from gateway.routes.chat import router as chat_router
    from gateway.routes.ingest import router as ingest_router
    from gateway.routes.admin import router as admin_router

    app.include_router(chat_router)
    app.include_router(ingest_router)
    app.include_router(admin_router)

    # ── Health check ──────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "sparkline-rag", "env": settings.app_env}

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "Sparkline AI — LLM + RAG",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "gateway.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
    )
