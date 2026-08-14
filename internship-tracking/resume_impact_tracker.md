# 📊 Sparkline Internship — Resume Impact Tracker

> **Goal:** Capture quantifiable metrics throughout the internship so the final resume bullet points are data-backed and impressive.
> **Last updated:** 2026-08-14

---

## 🏗️ Project Overview (for resume context)

**Project:** Sparkline AI — In-House LLM + RAG System  
**Role:** Backend / AI Systems Engineer (Intern)  
**Stack:** FastAPI, PostgreSQL, Redis, MinIO, Qdrant, Ollama/vLLM, sentence-transformers, BM25, CrossEncoder  
**Scope:** Fully local, enterprise-scale LLM + RAG system — no external AI APIs

---

## ✅ Metrics to Track (Populate as you go)

### System Architecture & Scale
- [x] Number of pilot users served: **6 pilot testers provisioned + 1 owner + 1 file admin** (as of 2026-08-13; replaced the 11 seeded placeholder accounts with the real roster. Release to testers moved from 2026-08-14 to the week of 2026-08-17)
- [x] Number of documents ingested into Qdrant: **2 real documents** (first live ingestion, 2026-08-07)
- [x] Total chunks stored in vector DB: **93 active chunks** (48 + 45, bge-large-en 1024-dim)
- [ ] Qdrant collection size (MB/GB — fill in after larger-scale ingestion)
- [x] Number of Docker containers in stack: **7** (postgres, redis, minio, qdrant, ollama, api, open-webui) — deployed together for the first time on the production GPU server, 2026-08-07

### RAG Pipeline Performance
- [ ] RAGAS Faithfulness score (target >0.80)
- [ ] RAGAS Answer Relevance score (target >0.75)
- [ ] RAGAS Context Precision score (target >0.70)
- [x] End-to-end query latency (warm): **~1.4s** average (auth → hybrid retrieval → rerank → LLM → citations), measured 2026-08-07 on real documents with qwen2.5-coder:14b fully GPU-resident
- [x] General-knowledge query latency after automatic routing: **~1.7s** (skips retrieval entirely) vs. **5.6–14.7s** before, when every such query ran a full RAG pass and then had to be rescued by a fallback (2026-08-14)
- [ ] Reranker CrossEncoder top-k accuracy improvement vs. no reranker (%)
- [ ] BM25 + dense hybrid RRF vs. dense-only recall improvement (%)

### Query Routing (added 2026-08-11 → 08-14)
- [x] Routing accuracy on the validation matrix: **13/13** queries routed correctly (document vs. general vs. blended), measured end-to-end through the live API
- [x] Routing threshold separation measured on the real corpus: in-corpus questions median **+5.89**, out-of-corpus median **-9.76** (cross-encoder logits) — a clean, measurable gap rather than a guessed cut-off
- [x] Reranker candidate pool after fixing fusion truncation: **5 → 20** candidates scored per query (the cross-encoder previously never saw 35 of the ~40 retrieved)
- [x] Retrieval score recovery on a representative query after the fusion fix: **-6.54 → +1.61** for the same question
- [x] Rollback path: **1 env variable** (`ROUTER_MODE=legacy`) restores previous behaviour with no code change — verified working
- [x] Routing floor corrected after a real miss: **-5.0 → -6.0** — a broadly worded question the documents *do* answer was scoring -5.45 and being sent to general knowledge. The misspelling was not the cause (the correctly spelled version scored -5.91, worse); the threshold was simply too strict. Risk is asymmetric — a general question wrongly given documents is caught by the existing fallback, a document question wrongly sent to general has no safety net (2026-08-13)

### Typo Tolerance (added 2026-08-13)
- [x] Misspelled in-corpus questions recovered: **12 of 12** now score *identically* to their correctly spelled versions (in-corpus median +5.89), against 3 of 4 previously falling below the routing floor and being answered from general knowledge
- [x] Correction latency: **0.05–0.4 ms** per query, in memory, **no model call and no GPU**
- [x] Correction source: **the ingested corpus vocabulary only**, rebuilt automatically on every document upload — **zero hardcoded word lists**, verified by ingesting a document never used in development and confirming typos in its vocabulary were corrected with no code change
- [x] Over-correction eliminated using the reranker's existing tokenizer as an English word list: **~21,000 words protected**, no new dependency ("tell me a joke" was being searched as "well me a joke")
- [x] General-question interference after the change: **zero** — all 15 out-of-corpus calibration questions scored byte-identically to before

### Ingestion Performance (added 2026-08-14)
- [x] Large-workbook ingestion: **~60 minutes → 58 seconds** for a 4.93 MB, 20-sheet, macro-enabled Excel file (120,000 rows), while indexing **2× more** of it
- [x] Embedding throughput: **~3 → ~43 chunks/sec (14×)** after granting the API container GPU access — the CUDA build was already present, the container had simply never been given the device
- [x] Cost driver identified as chunk count, not file size: a **16.3 MB** Word report produced **605** chunks (37s) while a **4.93 MB** spreadsheet produced **10,786**
- [x] Per-document index ceiling with proportional truncation: every one of 20 sheets retained **exactly 50 chunks**, so no sheet silently disappears

### Ingestion Pipeline
- [x] Number of document types supported: **10** — PDF (incl. OCR for scanned pages), DOCX, legacy DOC, XLSX, macro-enabled XLSM, legacy XLS, CSV, TSV, TXT, Markdown (2026-08-14; was 4, and two of those four were listed as supported while failing on every real file)
- [ ] Ingestion throughput (docs/min — measure once GPU arrives)
- [ ] Token-aware chunker: chunk size = 512 tokens, overlap = 64 tokens

### Access Control System (PDP/PEP)
- [ ] Number of access control rules/policies implemented
- [ ] Number of distinct roles modeled: admin, file_admin, pilot_user, scoped user
- [ ] Response time for PDP evaluation (ms) — measure once integrated end-to-end

### Reliability & Testing
- [x] Unit test coverage: **75/75 tests passing** (as of 2026-08-14; grew from 10 across the routing, typo-tolerance and document-format work)
- [x] Live regression suite: **22 passing / 0 failing** (grew from 17; the 5 additions cover typo tolerance end-to-end, including that a correctly spelled general question is not dragged into the documents) (`eval/precommit_checks.py` — session-history integrity, routing fallbacks, blended-mode behaviour, degenerate input, latency, audit logging)
- [x] Integration smoke test: **7/7 checks passing**
- [ ] Uptime / availability target (SLA if defined)
- [x] Production bugs found & fixed during first live deployment: **4** — (1) a document-visibility access-control bug where a document ingested as "public" was silently invisible to 100% of pilot users; (2) a background-task DB-session lifecycle bug that silently broke the keyword-search (BM25) index after every single document ingestion; (3) a missing Open WebUI Pipe Function on the new server, which would have bypassed access control and retrieval entirely and served ungrounded LLM answers; (4) a response-parsing bug that rendered every browser answer as a sources list with no answer text (2026-08-07)
- [x] Deployment-blocking issues caught by validating the frontend path separately from the API path: **2 of the 4** — both were invisible at the API level and would only have surfaced live in front of stakeholders
- [x] LLM backends evaluated under real GPU constraints before production selection: **2** (GPT-OSS 20B, qwen2.5-coder:14b) — rejected the larger model after it returned empty responses despite fitting fully in GPU memory
- [x] Additional defects found and fixed during the routing work: **5** — (1) the general-LLM agent was unreachable dead code, so every query including "hi" went through document RAG and was refused whenever the corpus didn't cover it; (2) rank fusion truncated the reranker's candidate pool to the answer size, so the cross-encoder scored 5 of ~40 candidates; (3) BM25 tokenization left punctuation attached, silently dropping keyword search from the hybrid merge for any question ending in "?"; (4) Open WebUI's internal title/tag-generation requests were consuming the full RAG pipeline and leaking into the chat; (5) follow-up detection was length-based, gluing self-contained questions onto unrelated document questions (2026-08-11 → 08-14)
- [x] Defects caught only by testing the browser path, not the API: **2 of 5** — reinforcing the earlier finding that API-level correctness does not imply a working user experience
- [x] Additional defects found and fixed during pilot-readiness work: **6** — (1) the chat integration logged in as each user with a password hardcoded in the source, so a user changing their own password silently broke their own chat; (2) typo correction was rewriting ordinary English words absent from the small corpus, turning "tell me a joke" into "well me a joke"; (3) a badly misspelled word retrieved the right passages but the model was shown the raw typo, refused, and the fallback then invented a definition for a word that does not exist; (4) macro-enabled Excel (.xlsm) was not an accepted format at all, despite being how most business spreadsheets are saved; (5) both pre-2007 Office formats were listed as supported while failing on every real file; (6) newly created users could log in but saw no models at all, because a chat-frontend model with no access rule configured is visible to administrators only (2026-08-13 → 08-14)
- [x] Provisioning steps required per user, discovered the hard way: **3** — a backend account, a chat-frontend account, and an explicit model permission. Completing only the first two leaves a user able to log in and see nothing
- [x] Bug found in my own fix before release, via the regression suite: **1** — the mixed-conversation case (a general question interleaved into a document conversation) was silently breaking follow-ups

### Collaboration & Integration
- [ ] Number of shared infrastructure components with Dhruv's enterprise adapters: **2** (PostgreSQL, FastAPI Orchestrator, Ollama)
- [x] API endpoints implemented: **14** across auth, user management, chat and ingestion — 4 added 2026-08-13 (service-token session issue, self-service password change, admin password reset, admin user creation)

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

• Designed and shipped automatic query routing that decides per message whether 
  to answer from the company's documents or from general knowledge, replacing a 
  keyword classifier under which the general-LLM path was unreachable and every 
  question — including "hi" — returned "I couldn't find this in the available 
  documents"; routed 13/13 validation queries correctly and cut general-question 
  latency from 5.6–14.7s to ~1.7s.

• Rejected keyword-based intent classification on the grounds that question 
  wording cannot indicate whether an answer exists in a corpus, and instead 
  routed on retrieval evidence — running retrieval first and thresholding the 
  cross-encoder relevance score — with thresholds empirically calibrated against 
  the real corpus (in-corpus median +5.89 vs. out-of-corpus -9.76) rather than 
  guessed.

• Diagnosed and fixed two latent retrieval defects while calibrating those 
  thresholds: rank fusion was truncating the reranker's candidate pool from ~40 
  to 5 before scoring, and BM25 tokenization left punctuation attached, silently 
  removing keyword search from the hybrid merge for any question ending in a 
  question mark.

• Grew automated coverage from 10 to 45 unit tests plus a 17-check live 
  regression suite, which caught a defect in my own routing fix — interleaved 
  general questions silently breaking document follow-ups — before it shipped.

• Shipped the routing change behind a single-environment-variable rollback, and 
  validated the browser path independently of the API, catching 2 defects that 
  were invisible at the API layer including an integration issue where the chat 
  frontend's own background requests were consuming the full RAG pipeline.

• Built typo tolerance for a document assistant by correcting queries only 
  against the vocabulary of the currently ingested corpus — rebuilt on every 
  upload, with no hardcoded dictionary — recovering 12 of 12 misspelled 
  in-corpus questions to scores identical to their correctly spelled versions, 
  in under 0.4ms per query with no model call; validated on a document the 
  feature had never seen to prove it generalises to new content.

• Eliminated over-correction in that feature by reusing the reranker's existing 
  21,000-word tokenizer vocabulary as an English word list, distinguishing "the 
  user misspelled something" from "the documents simply do not discuss this" 
  without adding a dependency or a list to maintain.

• Removed a shared password hardcoded in the chat integration — under which any 
  user changing their own password silently broke their own chat — replacing it 
  with service-token authentication so the front end never handles user 
  credentials, and adding the user-creation, password-change and admin-reset 
  endpoints that had not previously existed.

• Cut ingestion of a 5MB, 20-sheet, macro-enabled Excel workbook from ~60 
  minutes to 58 seconds (14x embedding throughput) by identifying that the 
  container shipped a CUDA build of PyTorch but had never been granted GPU 
  device access, while indexing twice as much of the file; kept the reranker on 
  CPU deliberately after hitting a real out-of-memory error on the shared card, 
  since it runs on every query.

• Expanded accepted document formats from 4 to 10 (adding macro-enabled Excel, 
  legacy Word and Excel, CSV, TSV, Markdown and plain text), dispatching on each 
  file's binary signature rather than its extension so misnamed uploads still 
  parse, after finding two formats were advertised as supported while failing on 
  every real file.

• Designed the routing contract between the RAG system and a colleague's ERP 
  integration, resolving overlapping coverage by routing on what a question asks 
  for — documents hold what is written down, the ERP holds what is recorded — 
  rather than on subject matter, and requiring adapters to self-assess coverage 
  since the orchestrator cannot see inside a system it does not own.
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
| 2026-08-11 | Root-caused the "everything is document RAG" defect; designed evidence-based routing | General-LLM agent was unreachable dead code; retrieval had no relevance floor. Built the threshold calibration harness |
| 2026-08-12 | Two retrieval-quality defects found and fixed via calibration | Fusion truncated the reranker's pool (5 of ~40 candidates scored); BM25 tokenizer left punctuation attached, dropping keyword search for most questions |
| 2026-08-13 | Automatic routing (evidence gate) implemented | Three score bands — grounded / blended / general — plus a general-knowledge fallback and a config-only rollback |
| 2026-08-14 | Browser validation, Open WebUI task-request fix, regression suite | 13/13 routing matrix correct; unit tests 10 → 45; 17-check live suite added |
| 2026-08-13 | Typo tolerance built and proven to generalise | 12/12 misspelled in-corpus questions recovered; corrections drawn only from the ingested corpus, verified on an unseen document |
| 2026-08-13 | Shared hardcoded password removed; account management added | Service-token auth so the pipeline never handles user passwords; create-user, change-password and admin-reset endpoints added; 6 pilot testers provisioned, 11 stale accounts retired |
| 2026-08-14 | Document format support widened 4 → 10 | Macro-enabled Excel, legacy Word/Excel, CSV/TSV, Markdown, plain text; dispatch by file signature rather than extension |
| 2026-08-14 | Embedding moved onto the GPU | 5MB/20-sheet workbook: ~60 min → 58s, 14x throughput; reranker kept on CPU after a real OOM on the shared card |
| 2026-08-14 | Document withdrawal (undo) added | Administrator can remove a document and all versions; vectors deleted before database rows so a partial failure leaves invisible orphans, not retrievable ones |
| 2026-08-14 | Pilot release rescheduled to week of 2026-08-17 | Extra time goes to loading the testers' real documents and re-measuring routing thresholds against them |
| 2026-08-14 | Enterprise routing contract agreed with Dhruv | Route on question type, not subject; adapters self-assess coverage; no ERP routing built until real data and per-user permissions exist |
| TBD | RAGAS evaluation run | Will populate faithfulness / relevance / precision metrics. **Must run after the routing change** — baselines measured under always-RAG behaviour would be invalid |

---

## 🔮 Upcoming High-Impact Work (good for resume)

- **Tool-calling repair (charts / Word / Excel export)** — the suite is built but non-functional: qwen2.5-coder:14b returns tool calls as message text rather than structured calls, so the executor never fires. Needs either a model with reliable tool-calling or a text-based parser — a self-contained, demonstrable piece of work
- **Move document upload into Open WebUI (planned next week)** — the administrator currently uploads through the FastAPI docs page on port 18000, which works but sits outside the interface everyone else uses. Migrating it into Open WebUI itself puts adding and withdrawing a document in the same place as the chat
- **Per-chat file upload with session-scoped retrieval** — let a user attach a file to a conversation and ask questions about it. Blocked today by access control rather than by parsing: pilot users resolve to full access, so the retrieval filter is active-version-only and any session document would be visible to every user. Needs session scoping in the Qdrant filter, a time-to-live for temporary chunks, and the pipeline to forward attachments it currently ignores. Roughly 1–2 days, and deliberately not attempted during a live pilot since it changes the code path that decides who can see what
- **Switch the serving model off the code-specialised variant** — qwen2.5-coder:14b is tuned for programming and is being used as a general assistant, which is the root cause of both the broken tool-calling and weak general knowledge; a general instruct model of the same size is a one-line change that addresses both, and does not affect routing since the relevance scores come from a separate cross-encoder on CPU
- **Per-user permissions before any ERP data is exposed** — the ERP integration reaches its database over a single shared connection, so wiring it in as-is would bypass the document-level access control entirely
- **Validate the legacy .doc path against a genuine pre-2007 Word file** — the reader is installed and wired, and failure is graceful, but no real .doc file was available to confirm the success path
- **Threshold recalibration as the corpus grows** — rerun `eval/calibrate_router.py` once real stakeholder documents land; current bands are measured against only 93 chunks
- **Async BM25 re-indexing** — improve ingestion scalability for large corpora
- **PII scanning via Microsoft Presidio** — data privacy / compliance angle
- **Streaming responses in Open WebUI pipeline** — UX improvement, measurable latency reduction
- **RAGAS evaluation run** — will produce the hard numbers for resume bullets
- **vLLM + GPU integration** — production-grade LLM serving, throughput benchmarks
