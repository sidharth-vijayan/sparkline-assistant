# 📊 Sparkline Internship — Resume Impact Tracker

> **Goal:** Capture quantifiable metrics throughout the internship so the final resume bullet points are data-backed and impressive.
> **Last updated:** 2026-08-20

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
- [x] Total chunks stored in vector DB: **186 active chunks** (bge-large-en 1024-dim; re-counted 2026-08-18 after the format and truncation work, up from 93)
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
- [x] Unit test coverage: **230/230 tests passing** (as of 2026-08-20; grew from 10. The last 4 are structural rather than behavioural: they assert that every method the ingestion pipeline calls on itself actually exists, which is the fault class that had silently broken every document upload while the whole suite passed)
- [x] Live regression suite: **21 passing / 1 failing / 5 human reads** across 23 checks (grew from 22). The single failure is deliberate and new: a mechanical check that the routing band can actually produce all three answer modes. It had been sitting as two soft "needs a human read" notes, which is how it survived unresolved — it now fails outright (`eval/precommit_checks.py`)
- [x] Integration smoke test: **7/7 checks passing**
- [x] Adversarial / prompt-injection suite: **0 failing** — 18 passing on 2026-08-19 from 11 passing / 4 failing when first run the same day, and re-run clean after the hardening work (16 passing / 0 failing / 3 human reads; the item count varies between runs because some export probes are conditional on the model emitting a tool call) (`eval/adversarial_checks.py`)
- [ ] Uptime / availability target (SLA if defined)
- [x] Production bugs found & fixed during first live deployment: **4** — (1) a document-visibility access-control bug where a document ingested as "public" was silently invisible to 100% of pilot users; (2) a background-task DB-session lifecycle bug that silently broke the keyword-search (BM25) index after every single document ingestion; (3) a missing Open WebUI Pipe Function on the new server, which would have bypassed access control and retrieval entirely and served ungrounded LLM answers; (4) a response-parsing bug that rendered every browser answer as a sources list with no answer text (2026-08-07)
- [x] Deployment-blocking issues caught by validating the frontend path separately from the API path: **2 of the 4** — both were invisible at the API level and would only have surfaced live in front of stakeholders
- [x] LLM backends evaluated under real GPU constraints before production selection: **3** (GPT-OSS 20B, qwen2.5-coder:14b, qwen2.5:14b) — rejected the largest after it returned empty responses despite fitting fully in GPU memory, then moved off the code-tuned model to the general instruction-tuned one of the same size, since the assistant answers policy and HR prose rather than code (2026-08-18)
- [x] Additional defects found and fixed during the routing work: **5** — (1) the general-LLM agent was unreachable dead code, so every query including "hi" went through document RAG and was refused whenever the corpus didn't cover it; (2) rank fusion truncated the reranker's candidate pool to the answer size, so the cross-encoder scored 5 of ~40 candidates; (3) BM25 tokenization left punctuation attached, silently dropping keyword search from the hybrid merge for any question ending in "?"; (4) Open WebUI's internal title/tag-generation requests were consuming the full RAG pipeline and leaking into the chat; (5) follow-up detection was length-based, gluing self-contained questions onto unrelated document questions (2026-08-11 → 08-14)
- [x] Defects caught only by testing the browser path, not the API: **2 of 5** — reinforcing the earlier finding that API-level correctness does not imply a working user experience
- [x] Additional defects found and fixed during pilot-readiness work: **6** — (1) the chat integration logged in as each user with a password hardcoded in the source, so a user changing their own password silently broke their own chat; (2) typo correction was rewriting ordinary English words absent from the small corpus, turning "tell me a joke" into "well me a joke"; (3) a badly misspelled word retrieved the right passages but the model was shown the raw typo, refused, and the fallback then invented a definition for a word that does not exist; (4) macro-enabled Excel (.xlsm) was not an accepted format at all, despite being how most business spreadsheets are saved; (5) both pre-2007 Office formats were listed as supported while failing on every real file; (6) newly created users could log in but saw no models at all, because a chat-frontend model with no access rule configured is visible to administrators only (2026-08-13 → 08-14)
- [x] Provisioning steps required per user, discovered the hard way: **3** — a backend account, a chat-frontend account, and an explicit model permission. Completing only the first two leaves a user able to log in and see nothing
- [x] Bug found in my own fix before release, via the regression suite: **1** — the mixed-conversation case (a general question interleaved into a document conversation) was silently breaking follow-ups

### Security & Operations Hardening (added 2026-08-18)
- [x] Credentials removed from the source tree: **1 administrator password** that was a literal default in `admin_tools/ingest_cli.py` — and therefore in a public repository, in two documentation files and in the smoke test. The account can ingest and withdraw documents for every user. Rotated, and the CLI now prompts rather than shipping a fallback
- [x] Secrets rotated across every location that must agree: **2** — the service token lives in `.env`, in the chat frontend's stored pipe configuration and in the running container's baked environment, so rotating it in one place alone silently breaks every chat
- [x] Services found reachable from the office LAN: **7 of 7**, now **2 of 9 by design** — the container platform published every port past the host firewall, so Postgres, Redis, Qdrant, MinIO, the API and the frontend all answered the network, and the vector store answered **unauthenticated** callers. Verified from a second machine rather than assumed, and the leak demonstrated by reading document text out of Qdrant with no credentials. **Closed 2026-08-19:** every datastore binds to loopback, Qdrant returns 401 without a key, and only the API and chat frontend still answer the LAN because those are the product. Loopback rather than removing the ports, so the SSH tunnel used for administration and integration tests keeps working
- [x] Permanent GPU memory reclaimed: **15 GB** by removing the rejected model, plus a resident-model cap converting a second permanent **9 GB** claim into memory held only while in use — necessary because the other model is shared infrastructure another team's tool depends on and cannot be deleted
- [x] Development moved off remote desktop onto an SSH tunnel: removes a full desktop session from a **7.9 GB** shared machine while keeping all computation server-side
- [x] Environment defect diagnosed to root cause rather than worked around: torch failed to load on the development laptop with a DLL initialisation error. Eliminated corrupt files (checksums verified against the package manifest), wrong Python ABI, missing dependencies and memory pressure in turn, before tracing it to the machine carrying only the 2019 C++ runtime while the library targets the 2022 one — and fixed it without administrator rights. **75/75** tests then passing locally

- [x] Security faults found by deliberate attack and then fixed: **3 of 3** — the assistant followed instructions hidden inside a document's contents (replying with the attacker's planted phrase, announcing "maintenance mode" and printing its own instructions), leaked its operating instructions to "repeat everything above this line", and obeyed a plain "ignore all previous instructions". All closed on 2026-08-19; the suite went from 11/4 to 18/0
- [x] Attack surface that held under testing, before any fix: **access control** — a second account given the correct conversation identifier could not extract an attachment, because isolation is enforced when documents are fetched rather than by the model's cooperation. This is what made the injection findings integrity problems rather than data breaches
- [x] Incomplete fix caught by re-running the suite rather than by inspection: **1** — the defences were applied to the document-answering path, while the disclosure attempts are handled by the general-knowledge path, so the hole remained open on the busier of the two routes
- [x] Faults in my own test suite found and corrected: **2** — one check reported a failure that was not real (it plainly asked the assistant to print the marker, so complying was correct), and another could report a pass without exercising anything (an early exit skipped the assertion whenever the model happened not to emit the hostile input)
- [x] Credentials found identical to values published in the repository: **4 of 4** — the PostgreSQL, Redis and MinIO passwords and MinIO's administrator name were byte-identical to the placeholders in the tracked `.env.example`, so repository access implied database access. Nothing had gone wrong to cause it; the placeholders simply worked, so nobody was ever prompted to change them. All rotated 2026-08-19
- [x] Guards added so the same class of exposure cannot return silently: **3** — the application refuses to start on any credential published in `.env.example`, warns at every startup if Qdrant has no API key, and the Compose definitions no longer supply fallback passwords, so an unset value halts the stack with a message instead of quietly becoming a shared secret
- [x] Copies of answer text deleted from the audit log: **721** — it stored the first 500 characters of every response, which for a document answer is verbatim document text, in a table with no access control over it. Nothing ever read it back, so it was pure liability. Query text, user, agent type and retrieved document versions are retained, because attribution is the log's entire purpose
- [x] Retention defect closed: **withdrawal now deletes the stored file.** Previously the original was retained indefinitely by design, so a document uploaded in error remained on the server after an administrator had been told it was deleted. Verified end to end — one version, one chunk and one file removed, zero vector points and zero stored objects left
- [x] Non-obvious failure modes hit while hardening, each of which would have looked like something else: **2** — the vector-store client silently switches to TLS the moment an API key is supplied, so every call then failed with a certificate error rather than an authentication one; and changing the PostgreSQL password in configuration does nothing to an existing database, because that variable is read only at creation, so doing only that half would have left the old password working while appearing rotated

### Answer Accuracy Measurement (added 2026-08-19)
- [x] Accuracy measures that need **no human-written answer**: **4** — routing accuracy, abstention rate, recall@k and self-consistency (`eval/accuracy_suite.py`). Built because the bottleneck on accuracy is a subject expert's time, not tooling, and everything measurable without them should not wait for them
- [x] First measured figures (2026-08-19): **routing accuracy 100%** (27/27 — 12/12 document questions, 15/15 general), **abstention rate 0%** (0/12 in-corpus questions left without a grounded answer), **self-consistency 0.997** mean answer similarity across 5 repeats
- [x] Measures deliberately reported as *not measured* rather than faked: **1** — recall@k needs one source filename per question. Inferring the expected document from what retrieval returned would score the system against its own output and always report 100%
- [ ] Answer **correctness** — the one thing none of the above establishes. A system can route perfectly, cite the right file, always answer and answer identically every time, and still be wrong. Scoped to a concrete request: ~30 questions confirmed by someone in HR or Finance, roughly 2 people × 2 hours
- [x] Privacy fault found in the evaluation tooling itself: **1** — `eval/ragas_runner.py` called `evaluate()` with no judge configured, so it would have defaulted to OpenAI and sent every question, generated answer and retrieved document passage off-site, while its own docstring stated evaluation was local. Nothing leaked: `ragas` and `datasets` were never pinned dependencies, so it had never run. Now passes a local judge and local embeddings explicitly, and refuses to start unless `LLM_BASE_URL` resolves to a local host
- [x] Reason the RAGAS scores are reported as directional rather than as evidence: the judge is an LLM, and on this hardware it is **the same model being graded** — it shares the system's blind spots and will approve its own mistakes. Paired with a human read of ~20 answers
- [x] Capacity limit observed rather than assumed during the accuracy run: the GPU held **15.4 of 16.3 GB** across three unrelated workloads, the embedding model hit CUDA OOM and correctly fell back to CPU, and `qwen2.5:14b` was running **19%/81% GPU/CPU**. Nothing failed — but response times on a card shared three ways are not the times the pilot should be judged on

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
| 2026-08-18 | Serving model switched to the general instruct variant; export feature re-diagnosed | The code-tuned model was the wrong instrument for policy and HR prose. Export was recorded as broken tool-calling; layer-by-layer testing showed only delivery is missing |
| 2026-08-19 | Per-chat attachments delivered with isolation proven under a live model | Separate store rather than tagging; no expiry, reclamation by reconciliation; attachments given a guaranteed share of answer context rather than a score bonus |
| 2026-08-19 | Network exposure closed after being demonstrated, not argued | Document text read out of Qdrant from a laptop on the office LAN with no credentials; datastores moved to loopback, Qdrant keyed, 4 repo-published credentials rotated, 3 startup guards added |
| 2026-08-19 | Audit log slimmed and deletion made real | 721 stored answer copies dropped from the audit table; document withdrawal now purges the stored original instead of retaining it indefinitely |
| 2026-08-19 | Permissions became editable after upload | PATCH endpoint plus a CLI command, writing both Postgres and the Qdrant payload — what allows the pilot corpus to be loaded before HR supplies department data |
| 2026-08-19 | First accuracy figures produced, with no ground truth required | Routing 100%, abstention 0%, self-consistency 0.997. Also found and fixed that the RAGAS harness would have sent document text to OpenAI |
| 2026-08-19 | Two pilot-blocking faults found by using the system, not reading it | Every document upload was failing (a method had drifted out of its class); the admin ingest CLI could not parse an argument (Typer/click mismatch). Both on main with the suite green |
| TBD | RAGAS evaluation run | Will populate faithfulness / relevance / precision metrics. **Must run after the routing change** — baselines measured under always-RAG behaviour would be invalid |

---

## 🔮 Upcoming High-Impact Work (good for resume)

- **File export delivery — DONE 2026-08-19.** The earlier diagnosis was wrong. Tested layer by layer on 2026-08-18: the model emits correct tool calls (3 of 4 probes), the generators produce valid .docx/.xlsx that reopen cleanly, and the API returns them. The break is delivery — the frontend layer forwards only answer text, so the file is built and discarded while the model announces success. Needs object storage, an authenticated download route, and a link in the reply. Chart generation still unverified
- **A dedicated admin front end — SUPERSEDES "move upload into Open WebUI".** The administrator still uploads through the FastAPI docs page on port 18000. The destination is no longer Open WebUI but a single static page served by FastAPI at `/admin/ui`: upload, list, withdraw, edit permissions and read the audit log. No new container and no new service — deliberately, since a page talking directly to storage would recreate the LAN exposure closed on 2026-08-19. Every endpoint it needs already exists except an authenticated route to download an original file, so this is roughly a day and is mostly front-end work
- **Per-chat file upload with session-scoped retrieval — BUILT 2026-08-19.** Delivered end to end: upload, isolated retrieval, admin listing, deletion, and a reclamation sweep. Proven with a live model that a second user supplying the correct conversation ID retrieves nothing. Remaining: switch the upload control on in the chat interface, a one-setting change held back for deliberate review
- **Switch the serving model off the code-specialised variant — DONE 2026-08-18.** Retained here for the reasoning: qwen2.5-coder:14b is tuned for programming and is being used as a general assistant, which is the root cause of both the broken tool-calling and weak general knowledge; a general instruct model of the same size is a one-line change that addresses both, and does not affect routing since the relevance scores come from a separate cross-encoder on CPU
- **Per-user permissions before any ERP data is exposed** — the ERP integration reaches its database over a single shared connection, so wiring it in as-is would bypass the document-level access control entirely
- **Validate the legacy .doc path against a genuine pre-2007 Word file** — the reader is installed and wired, and failure is graceful, but no real .doc file was available to confirm the success path
- **Threshold recalibration as the corpus grows** — rerun `eval/calibrate_router.py` once real stakeholder documents land; current bands are measured against only 93 chunks
- **Async BM25 re-indexing** — improve ingestion scalability for large corpora
- **PII scanning via Microsoft Presidio** — data privacy / compliance angle
- **Streaming responses in Open WebUI pipeline** — UX improvement, measurable latency reduction
- **RAGAS evaluation run** — will produce faithfulness and answer-relevance numbers. Both are reference-free, so this is blocked only on real documents, not on a subject expert; context precision additionally needs ~30 confirmed answers. The harness itself was made verifiably local on 2026-08-19, having been one `evaluate()` call away from sending document text to OpenAI
- **Answer correctness against a confirmed set** — the one measurement that cannot be automated, and the honest gap in every accuracy figure produced so far. ~30 questions confirmed by someone in HR or Finance, paired with a human read of ~20 generated answers alongside the machine scores
- **vLLM + GPU integration** — production-grade LLM serving, throughput benchmarks

---

## 🧩 Challenges Faced and Key Decisions Made

*Updated at the end of each working day. Kept short on purpose.*

### Week of 2026-08-17

**Challenges**

- One 16 GB GPU shared by three unrelated workloads, two of them other people's. The computer-vision stack loads the card directly, so it is invisible to the model server's own reporting and only shows up in the driver's.
- A model the whole company depends on could not be removed to free memory, so the saving had to come from capping how many models stay resident instead.
- Two defects that looked like one: the export feature was recorded as a broken model integration, but testing each layer separately showed the model and the file generators both work and only delivery is missing.
- A feature can pass review and its own tests and still be wrong. Attachment retrieval looked correct until it was tried with the vague questions people actually ask, which returned the wrong documents entirely.
- The chat interface re-sends a conversation's whole file list on every message, so the obvious implementation would re-process the same document on every turn.

**Key decisions**

- Keep per-conversation files in a **separate vector collection** rather than tagging them in the main one, so a leak is structurally impossible instead of depending on every filter being correct. Every account in the system currently resolves to full access, which made this the deciding factor.
- **No expiry on attachments.** A file disappearing mid-conversation is worse than storage cost. Reclamation is instead a sweep that compares live conversations against what is held — with three refuse-to-act safeguards, since the failure mode is deleting everything rather than deleting too little.
- Give attachments a **guaranteed share of the answer context, not a score bonus**. One file cannot out-compete a whole corpus on a vague question, and boosting scores would have distorted genuine relevance.
- Decide from **evidence, not intent**: an attachment overrides the usual confidence threshold only when it was actually retrieved, so an unrelated later question in the same conversation still answers normally.
- Send the **original file** to our own readers rather than the interface's extracted text, because generic extraction flattens spreadsheets and the document set is spreadsheet-heavy.
- Fixed conversation identity to the conversation rather than the user. This also resolved a live defect where one conversation's history was being read back as context in another.
- Hold the interface's upload control **off** until the work is reviewed, even though the feature is finished.

### 2026-08-19 (later)

**Challenges**

- Closing the file-export path meant solving a problem the obvious design does not: a link in a chat window is followed by a browser, which cannot send an authorisation header, so the link has to carry its own narrowly-scoped proof rather than relying on the session.
- Adversarial testing found the assistant **is** vulnerable to instructions hidden inside document contents. It answered with the planted phrase, announced it was in maintenance mode, and printed its own instructions. Testing by eye would have missed this; planting a distinctive marker is what made it a hard pass/fail.

**Key decisions**

- Store generated files under a path that begins with the owner's identity, so there is no ownership check to forget — a different person resolving the same file reference looks somewhere else and finds nothing.
- Give a download link a proof that names **one file and one person**, so a link that leaks grants that single file rather than the account.
- Report a failed export in the chat rather than staying silent, because the model has already told the user it worked.
- Make reclamation of attachments possible at all by giving the service read-only sight of the chat records — read-only because it belongs to the frontend, and nothing here should be able to write to it.
- **Not** fixing the injection findings in the same pass. They need prompt restructuring and output checks, and are worth deciding deliberately rather than patching at the end of a long day. Access control held throughout, which is what makes that deferral defensible.

### 2026-08-19 (close of day)

**Challenges**

- The assistant could be made to follow instructions hidden in a document's contents, and to recite its own operating instructions on request. Both were found by deliberate probing, not by review.
- The first fix looked complete and was not. Protection was added to the document-answering path, but the disclosure attempts are handled by the general-knowledge path, which was still unprotected — so the hole stayed open on the busier of the two routes.
- One probe reported a failure that was not real: it plainly asked the assistant to print a phrase, so complying was correct. A test that cannot tell obedience from ordinary helpfulness is worse than no test.

**Key decisions**

- Mark retrieved passages as **data with visible boundaries** rather than relying on layout, and strip the boundary marker from the content first, since a document containing it could otherwise break out of its own region.
- State an explicit order of precedence in the instructions: text that arrives inside a document or a question and reads like a command is material to report on, never a command to follow.
- Keep the final safety check **narrow on purpose**. Phrases that appear in the instructions but are also ordinary things to say were excluded, because replacing a correct answer with a refusal is a worse failure than the disclosure it prevents.
- Re-ran the full behavioural suite after changing the instructions, on the basis that wording changes are exactly what quietly breaks routing and refusal handling. No regressions.
- Turned the per-conversation upload control on now that the isolation and the injection defences are both demonstrated, rather than shipping it earlier on the strength of the code alone.

### 2026-08-19 (hardening, deletion and accuracy)

**Challenges**

- The office-LAN exposure was real and demonstrable, not theoretical. From a laptop with no credentials, on the ordinary network, the text of an ingested document came straight out of the vector store. Proving it before fixing it is what made it a finding rather than an opinion — and what made it defensible to report upward.
- The credentials were not weak, which would have been an ordinary mistake. They were byte-identical to the placeholders published in the repository's own example file, so anyone with repository access held them. Nothing had gone wrong to cause it: the placeholders simply worked, so nobody was ever prompted to change them.
- The vector-store client silently switches to encrypted transport the moment an API key is supplied, assuming a key implies a cloud service. Against a plaintext local instance every call then failed with a certificate error, which reads as a broken certificate rather than the library having changed protocol underneath me. It cost more time than anything else in the task.
- Changing the database password in configuration does nothing to a database that already exists, because that variable is only read at creation. Doing only that half would have left the old password working while every appearance suggested rotation had succeeded — the most misleading possible outcome.
- Two pre-existing faults were found only because working uploads were needed to test something else: every document upload was failing outright, and the administrator's upload tool could not parse a single argument. Both were on main with the entire suite passing, and neither was visible from reading the code. The first sign otherwise would have been an upload failing in front of the testers.
- The routing band turned out to be recorded two ways in the project's own documents — as a deliberate decision on 2026-08-12 and as an open defect on 2026-08-18. Resolving it needs the intent settled first, not a new number.

**Key decisions**

- Bind the datastores to **loopback rather than removing their ports**. Removing them looks tidier but an SSH tunnel terminates on loopback, so it would have broken administration and the integration tests for no security gain. Only the API and the chat interface still answer the network, because those are the product.
- **Refuse to start** on a credential published in the repository, rather than warning. Unlike a measurement there is nothing to wait for — any random value is a correct value — so there is no reason to let the service run while the fix is pending.
- **Warn, not refuse**, on a missing vector-store key. Blank is defensible on a laptop where the port is on loopback anyway, and refusing there would push people toward pasting a shared key into local config files.
- Choose **genuine deletion over retention**, and record the cost rather than gloss it: we can no longer prove after the fact what was ingested. Keeping the original of a file somebody asked to have removed is the outcome the policy exists to prevent.
- Order withdrawal **vectors, then files, then records**, so an interruption always leaves a state a retry can finish. The reverse would strand files with nothing pointing at them — unfindable, and exactly the retention problem being solved.
- Write **both stores** when permissions change. Retrieval filters on the vector payload, so updating only the database would produce a system that reports a restriction it does not enforce — worse than no restriction, because it reads as safe.
- Measure accuracy on **what needs no human-written answer** rather than waiting for a subject expert, and say plainly in the tool's own output that none of it establishes correctness. Four measures today; correctness scoped to a concrete ask of ~30 questions.
- Have recall@k **report itself unmeasured** rather than infer the expected document from what retrieval returned, which would score the system against its own output and always report success.
- Make the routing-band problem **loud rather than resolved**: a startup warning plus a regression check that fails. The numbers themselves come from calibration against the real corpus, and inventing a pair to quiet the check is how the band went wrong in the first place.
- Replace the command-line library with the standard library rather than pinning a version. A pin holds only until the next dependency resolve and needs the container rebuilt to take effect; the built-in alternative cannot drift and needs nothing installed.
- Add a **structural** test — every method the ingestion pipeline calls on itself must exist — rather than a test for the one missing piece, so the whole fault class is caught.
