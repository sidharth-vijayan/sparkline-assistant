# Sparkline AI — In-House LLM + RAG System

Fully local, enterprise-scale LLM + RAG system for Sparkline. No external AI APIs.

## Architecture

```
Open WebUI (Frontend)
    ↓  inlet() pipeline intercept
FastAPI Orchestrator (/v1/chat/completions)
    ↓  two-step router (classify → dispatch)
    ├── General LLM Agent          → Ollama/vLLM (OpenAI-compatible)
    ├── Document RAG Agent         → Qdrant + BM25 + CrossEncoder + LLM
    └── Enterprise Agent (stub)    → Dhruv's MCP adapters
         ↓  shared infrastructure
         PostgreSQL | Redis | MinIO | Qdrant | Ollama
```

## Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Open WebUI + custom Pipeline |
| Orchestrator | FastAPI + plain Python router |
| LLM Serving | Ollama (dev) → vLLM (prod), OpenAI-compatible |
| LLM Model | Qwen2.5 14B Instruct Q4_K_M |
| Embeddings | BAAI/bge-large-en (sentence-transformers) |
| Vector DB | Qdrant |
| Keyword Search | BM25 (rank-bm25) |
| Hybrid Fusion | Reciprocal Rank Fusion (RRF) |
| Reranker | CrossEncoder (ms-marco-MiniLM-L-6-v2) |
| Document Parsing | pdfplumber, PyMuPDF, python-docx, openpyxl |
| OCR | Tesseract |
| Charts | matplotlib + pandas (sandboxed) |
| Export | python-docx, openpyxl |
| Auth | JWT (HS256) |
| DB | PostgreSQL (shared with Dhruv's adapters) |
| Session | Redis (Document RAG only — not shared) |
| File Storage | MinIO (non-destructive versioning) |
| Access Control | PDP/PEP (custom, Qdrant-enforced at retrieval) |
| Evaluation | RAGAS |

## Quick Start

### 1. Prerequisites

- Docker Desktop installed and running
- Python 3.11+
- Tesseract OCR (for scanned documents): [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)
- (Week 3+) Ollama installed: https://ollama.com

### 2. Start Infrastructure

```bash
# CPU-only (Weeks 1–2, no GPU needed)
docker compose up -d postgres redis minio qdrant

# With GPU (Week 3+, when RTX 5060 Ti arrives)
docker compose --profile gpu up -d
```

### 3. Install Python Dependencies

```bash
pip install poetry
poetry install
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values (defaults work for local dev)
```

### 5. Initialize Database + Seed Pilot Users

```bash
python -m db.init_db
```

This creates all tables and seeds the 10 pilot users with the temporary password `Sparkline@2025`.

### 6. Pull the LLM Model (Week 3+, requires Ollama)

```bash
ollama pull qwen2.5:14b
```

### 7. Run the API

```bash
# Development (with auto-reload)
uvicorn gateway.main:app --reload

# Or via Docker
docker compose up -d api
```

API docs: http://localhost:8000/docs

### 8. Install Open WebUI Pipeline

1. Open WebUI → Workspace → Pipelines → Import
2. Select `open_webui_pipeline/sparkline_pipeline.py`
3. Set `SPARKLINE_API_URL=http://localhost:8000`

---

## Ingesting Documents (File Admin)

### Via CLI

```bash
# Upload a public document
python -m admin_tools.ingest_cli upload reports/Q3_Report.pdf --public

# Upload with department restriction
python -m admin_tools.ingest_cli upload hr/leave_policy.pdf --departments "HR" --designations "Manager,Director"

# List all documents
python -m admin_tools.ingest_cli list

# Rebuild BM25 index
python -m admin_tools.ingest_cli rebuild-bm25
```

### Via API

```bash
curl -X POST http://localhost:8000/admin/ingest \
  -H "Authorization: Bearer <token>" \
  -F "file=@document.pdf" \
  -F "is_public=true"
```

---

## Access Control (PDP/PEP)

Access control is built as active infrastructure, not a future add-on.

### How it works

1. **PDP** (`access_control/pdp.py`): Evaluates user attributes + request intent → allow/deny + scope
2. **PEP** (`access_control/pep.py`): Converts decision into Qdrant filter → restricts retrieval

### Pilot Phase (Current)

The 10 pilot users have `default_role='pilot_user'` and no department/designation yet.  
**Temporary stand-in:** pilot users get full corpus access.  
**Real restriction activates** when department/designation are populated in the `users` table.

To set a user's department:
```bash
curl -X PATCH http://localhost:8000/users/<user_id> \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"department": "HR", "designation": "Manager"}'
```

### Default Deny

If the PDP cannot determine a user's permitted scope, the decision is DENY.  
This is enforced before Qdrant is ever queried.

---

## Document Versioning

When a new version of a file is uploaded:
- Old MinIO object is **never deleted** (permanent audit record)
- Old Qdrant chunks are **soft-deactivated** (`is_active_version=False`)
- New chunks are embedded and upserted as the active version
- The PEP always includes `is_active_version=True` in every query filter

---

## Citation Schema

Every RAG answer includes citations:

```json
{
  "answer": "The Q3 revenue was ₹42 crore...",
  "citations": [
    {
      "document_name": "Q3_Report.pdf",
      "page_number": 7,
      "version_uploaded_at": "2025-07-15T10:32:00Z",
      "chunk_text_preview": "Q3 revenue stood at ₹42 crore...",
      "rerank_score": 0.94
    }
  ]
}
```

---

## Switching Backends (Ollama → vLLM)

Change only two lines in `.env` — zero code changes:

```bash
LLM_BASE_URL=http://<vllm_host>:<port>/v1
LLM_MODEL_NAME=<model_name_on_vllm>
```

Same applies to embedding device (`EMBEDDING_DEVICE=cuda`), reranker device, and all other config.

---

## Evaluation

```bash
# Run RAGAS evaluation on the golden set
python -m eval.ragas_runner --input eval/golden_set.json --output eval/results.json
```

Target metrics (Week 8):
- Faithfulness > 0.80
- Answer Relevance > 0.75
- Context Precision > 0.70

---

## Project Structure

```
sparkline/
├── config/settings.py           # All config (Pydantic BaseSettings)
├── db/models.py                 # SQLAlchemy ORM models
├── db/init_db.py                # DB init + pilot user seed
├── services/                    # Infrastructure wrappers
│   ├── llm_client.py            # OpenAI-compatible LLM client
│   ├── embedding_service.py     # BGE embedding service
│   ├── qdrant_service.py        # Qdrant vector store
│   ├── minio_service.py         # MinIO file storage
│   ├── redis_service.py         # Session state
│   └── postgres_service.py      # DB session factory
├── ingestion/                   # Document ingestion pipeline
│   ├── parsers/                 # PDF, DOCX, Excel, OCR parsers
│   ├── chunker.py               # Token-aware chunker
│   ├── embedder.py              # Chunk embedding + Qdrant upsert
│   ├── bm25_index.py            # BM25 keyword index
│   └── pipeline.py              # Top-level ingestion orchestrator
├── retrieval/                   # Retrieval pipeline
│   ├── hybrid_retrieval.py      # BM25 + dense + RRF
│   ├── reranker.py              # CrossEncoder reranker
│   └── citation_builder.py     # Citation assembly
├── access_control/              # PDP/PEP access control
│   ├── pdp.py                   # Policy Decision Point
│   ├── pep.py                   # Policy Enforcement Point
│   └── intent_classifier.py    # Query intent classification
├── agents/                      # LLM agents
│   ├── general_llm_agent.py     # General Q&A agent
│   ├── document_rag_agent.py    # Full RAG agent
│   ├── enterprise_agent_interface.py  # Interface for Dhruv's adapters
│   └── tool_executor.py         # Tool-calling loop
├── tools/                       # LLM tools
│   ├── chart_tool.py            # matplotlib chart generation
│   ├── export_tool.py           # Word/Excel export
│   └── sandbox.py               # Sandboxed Python executor
├── router/query_router.py       # Two-step classifier → dispatch
├── gateway/                     # FastAPI gateway
│   ├── main.py                  # App entry point
│   ├── routes/chat.py           # /v1/chat/completions
│   ├── routes/ingest.py         # Admin ingestion routes
│   ├── routes/admin.py          # User management + audit log
│   ├── middleware/auth.py        # JWT authentication
│   └── middleware/audit.py      # Audit logging
├── open_webui_pipeline/         # Open WebUI integration
│   └── sparkline_pipeline.py    # inlet() pipeline
├── admin_tools/ingest_cli.py    # CLI for file admin
├── eval/                        # Evaluation
│   ├── golden_set.json          # Golden eval queries
│   └── ragas_runner.py          # RAGAS runner
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Shared Infrastructure with Dhruv's Enterprise Adapters

| Resource | Shared? | Notes |
|----------|---------|-------|
| PostgreSQL | ✅ Yes | Shared users, audit_log tables |
| FastAPI Orchestrator | ✅ Yes | Single entry point, shared router |
| Redis | ❌ No | Document RAG only |
| Qdrant | ❌ No | Document RAG only |
| MinIO | ❌ No | Document RAG only |
| LLM (Ollama) | ✅ Yes | Same endpoint, config-driven |

Dhruv's enterprise adapters implement `EnterpriseAgentInterface` in  
`agents/enterprise_agent_interface.py` and slot into the orchestrator without  
touching any shared infrastructure code.

---

## Pilot Users

All 10 pilot users are pre-seeded by `db/init_db.py`:

| Username | Full Name |
|----------|-----------|
| siddharth.doshi | Siddharth Doshi |
| shruti.doshi | Shruti Doshi |
| sandeep.pansare | Sandeep Pansare |
| ajit.mahabare | Ajit Mahabare |
| amogh.doshi | Amogh Doshi |
| parag.finance | Parag Finance |
| suraj.finance | Suraj Finance |
| vikas.ranaware | Vikas Ranaware |
| yojana | Yojana |
| roshni | Roshni |

**Default password:** `Sparkline@2025` — change on first login.

---

## Known Limitations & Next Steps

- **PII scanning** (Microsoft Presidio) is planned for a post-pilot hardening phase
- **Redis session sharing** with Dhruv's enterprise adapters: decision deferred to Week 6
- **BM25 re-indexing**: currently sync during ingestion; move to a background task for large corpora
- **Open WebUI Pipeline**: currently intercepts and pre-computes answers; proper streaming support is a follow-up
- **Shared policy engine**: PDP/PEP can be exposed as a service if Dhruv's adapters need document-level policy checks — architecture is ready, no changes needed
