# 📊 Sparkline Internship — Resume Impact Tracker

> **Goal:** Capture quantifiable metrics throughout the internship so the final resume bullet points are data-backed and impressive.
> **Last updated:** 2026-08-07

---

## 🏗️ Project Overview (for resume context)

**Project:** Sparkline AI — In-House LLM + RAG System  
**Role:** Backend / AI Systems Engineer (Intern)  
**Stack:** FastAPI, PostgreSQL, Redis, MinIO, Qdrant, Ollama/vLLM, sentence-transformers, BM25, CrossEncoder  
**Scope:** Fully local, enterprise-scale LLM + RAG system — no external AI APIs

---

## ✅ Metrics to Track (Populate as you go)

### System Architecture & Scale
- [x] Number of pilot users served: **11 seeded** (10 pilot + 1 file admin, as of 2026-08-07)
- [x] Number of documents ingested into Qdrant: **2 real documents** (first live ingestion, 2026-08-07)
- [x] Total chunks stored in vector DB: **93 active chunks** (48 + 45, bge-large-en 1024-dim)
- [ ] Qdrant collection size (MB/GB — fill in after larger-scale ingestion)
- [x] Number of Docker containers in stack: **7** (postgres, redis, minio, qdrant, ollama, api, open-webui) — deployed together for the first time on the production GPU server, 2026-08-07

### RAG Pipeline Performance
- [ ] RAGAS Faithfulness score (target >0.80)
- [ ] RAGAS Answer Relevance score (target >0.75)
- [ ] RAGAS Context Precision score (target >0.70)
- [x] End-to-end query latency (warm): **~1.4s** average (auth → hybrid retrieval → rerank → LLM → citations), measured 2026-08-07 on real documents with qwen2.5-coder:14b fully GPU-resident
- [ ] Reranker CrossEncoder top-k accuracy improvement vs. no reranker (%)
- [ ] BM25 + dense hybrid RRF vs. dense-only recall improvement (%)

### Ingestion Pipeline
- [ ] Number of document types supported: **4** (PDF, DOCX, Excel, OCR scanned PDFs)
- [ ] Ingestion throughput (docs/min — measure once GPU arrives)
- [ ] Token-aware chunker: chunk size = 512 tokens, overlap = 64 tokens

### Access Control System (PDP/PEP)
- [ ] Number of access control rules/policies implemented
- [ ] Number of distinct roles modeled: admin, file_admin, pilot_user, scoped user
- [ ] Response time for PDP evaluation (ms) — measure once integrated end-to-end

### Reliability & Testing
- [x] Unit test coverage: **10/10 tests passing** (as of 2026-07-30)
- [ ] Integration test count (add as you write more)
- [ ] Uptime / availability target (SLA if defined)
- [x] Production bugs found & fixed during first live deployment: **4** — (1) a document-visibility access-control bug where a document ingested as "public" was silently invisible to 100% of pilot users; (2) a background-task DB-session lifecycle bug that silently broke the keyword-search (BM25) index after every single document ingestion; (3) a missing Open WebUI Pipe Function on the new server, which would have bypassed access control and retrieval entirely and served ungrounded LLM answers; (4) a response-parsing bug that rendered every browser answer as a sources list with no answer text (2026-08-07)
- [x] Deployment-blocking issues caught by validating the frontend path separately from the API path: **2 of the 4** — both were invisible at the API level and would only have surfaced live in front of stakeholders
- [x] LLM backends evaluated under real GPU constraints before production selection: **2** (GPT-OSS 20B, qwen2.5-coder:14b) — rejected the larger model after it returned empty responses despite fitting fully in GPU memory

### Collaboration & Integration
- [ ] Number of shared infrastructure components with Dhruv's enterprise adapters: **2** (PostgreSQL, FastAPI Orchestrator, Ollama)
- [ ] API endpoints implemented (count GET/POST/PATCH in gateway/routes/)

---

## 📝 Draft Resume Bullets (fill in `X` as metrics become available)

These are templates — update the `X` values as you measure them:

```
• Built a fully local enterprise RAG system serving 10 pilot users, integrating 
  Qdrant vector search, BM25 keyword ranking, and CrossEncoder reranking with 
  Reciprocal Rank Fusion — achieving RAGAS faithfulness of X.XX and answer 
  relevance of X.XX.

• Designed and implemented a token-aware document ingestion pipeline supporting 
  4 file formats (PDF, DOCX, Excel, scanned OCR), chunking X,XXX documents into 
  X,XXX,XXX vector embeddings stored in Qdrant.

• Architected a custom PDP/PEP access-control layer that enforces document-level 
  permission policies at retrieval time using Qdrant filters, eliminating the 
  need for post-query filtering and reducing unauthorized data exposure risk.

• Reduced RAG response latency by X% by implementing hybrid BM25 + dense 
  retrieval with RRF fusion and CrossEncoder reranking, versus naive 
  dense-only retrieval.

• Delivered a production-ready FastAPI orchestrator with JWT auth, audit 
  logging, and an OpenAI-compatible /v1/chat/completions endpoint that 
  seamlessly switches between Ollama (dev) and vLLM (prod) backends via 
  a single .env change.

• Wrote X unit and integration tests covering chunking, access control, 
  citation building, and end-to-end ingestion, achieving X% pass rate in CI.

• Deployed a 7-container production stack (PostgreSQL, Redis, MinIO, Qdrant, 
  Ollama, FastAPI, Open WebUI) to a shared multi-tenant GPU server, evaluating 
  a 20B- and a 14B-parameter LLM against real (contended) GPU memory before 
  selecting a production backend based on empirical correctness and latency 
  testing rather than spec-sheet capacity alone.

• Found and fixed 4 production bugs during first live-data validation — including 
  a document-visibility access-control bug that silently hid ingested documents 
  from 100% of pilot users, and a misconfigured frontend integration that would 
  have bypassed access control and retrieval entirely to serve ungrounded LLM 
  answers — all caught and resolved before the pipeline reached stakeholders.

• Caught 2 demo-blocking defects that were invisible at the API layer by 
  validating the browser-facing chat path independently rather than assuming 
  API-level test coverage implied a working user experience.

• Achieved ~1.4-second average end-to-end RAG query latency (auth, hybrid 
  retrieval, reranking, LLM generation, and citation assembly) on real ingested 
  documents, down from 3+ minutes on a cold-started, incorrectly-sized model.
```

---

## 🗓️ Milestones Log

| Date | Milestone | Notes |
|------|-----------|-------|
| 2026-07-28 | Docker infrastructure tested (postgres, redis, minio, qdrant) | All services up via docker compose |
| 2026-07-30 | Unit test suite fixed and passing | 10/10 tests pass — fixed pytest pythonpath issue |
| 2026-08-07 | GPU (RTX 5060 Ti) arrives; full stack deployed to production shared server | First deployment on shared infra; all 7 containers healthy |
| 2026-08-07 | LLM backend selected under real GPU constraints | Evaluated GPT-OSS 20B vs. qwen2.5-coder:14b; picked the latter after the former returned empty responses despite fitting on GPU |
| 2026-08-07 | First real document ingestion + end-to-end RAG validation | 2 documents, 93 chunks; ~1.4s warm query latency with correct citations |
| 2026-08-07 | Found and fixed 4 production bugs pre-demo | Document-visibility access bug, BM25 background-task session bug, missing Open WebUI pipe function, and browser answer-parsing bug |
| 2026-08-07 | Open WebUI browser chat validated end-to-end on production | "Sparkline RAG" routing through access control, retrieval, and reranking with cited answers |
| TBD | RAGAS evaluation run | Will populate faithfulness / relevance / precision metrics |

---

## 🔮 Upcoming High-Impact Work (good for resume)

- **Async BM25 re-indexing** — improve ingestion scalability for large corpora
- **PII scanning via Microsoft Presidio** — data privacy / compliance angle
- **Streaming responses in Open WebUI pipeline** — UX improvement, measurable latency reduction
- **RAGAS evaluation run** — will produce the hard numbers for resume bullets
- **vLLM + GPU integration** — production-grade LLM serving, throughput benchmarks
