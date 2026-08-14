# Pilot 1 — System Baseline

**Snapshot taken:** 2026-08-14. The figures below were measured on that date; the release to the 6 testers was subsequently moved to the week of 2026-08-17, so this is the state the system was in immediately *before* pilot 1 rather than on its first day of use. The measured values are unchanged.
**Repository state:** `main` @ `baef833`, 137 commits, first commit 2026-07-23
**Host:** SEPL-PC

## What this file is for

This is the **frozen measurement of the system as it shipped to pilot 1**. Its only purpose is to
make the work done *after* this release measurable: when pilot 2 ships, re-run the commands in
[How to regenerate these numbers](#how-to-regenerate-these-numbers), fill the `Pilot 2` column, and
the improvement is visible without argument or recollection.

Two rules keep it useful:

1. **Never edit the Pilot 1 column.** If a number here turns out to have been measured wrongly, add
   a footnote — do not correct it. A baseline that gets retouched cannot prove anything.
2. **Measure pilot 2 the same way, on the same corpus, or say plainly that you did not.** Most
   metrics below are corpus-dependent. A latency win measured on a different document set is not a
   win.

Empty columns are intentional. Add `Pilot 3`, `Pilot 4` … as further releases land.

---

## Part A — The GPU server

Hardware is expected to stay fixed across pilots. It is recorded because it is the ceiling every
software number below is measured against, and because the headroom finding in
[A.4](#a4-gpu-headroom--the-binding-constraint) is currently the tightest constraint on the project.

### A.1 Machine

| Specification | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| Hostname | SEPL-PC | | |
| OS | Ubuntu 24.04.4 LTS (noble) | | |
| Kernel | 7.0.0-28-generic | | |
| CPU | Intel Core i7-14700K | | |
| Cores / threads | 20 cores / 28 threads | | |
| CPU max clock | 5.60 GHz | | |
| System RAM | 31 GiB | | |
| Swap | 8.0 GiB | | |
| Root disk | 915 GB NVMe (`/dev/nvme0n1p2`) | | |
| Disk free | 163 GB free of 915 GB (82% used) | | |

### A.2 GPU

| Specification | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti | | |
| VRAM | 16311 MiB (16 GB) | | |
| Driver | 595.84 | | |
| CUDA (driver) | 13.2 | | |
| Compute capability | 12.0 | | |
| Power cap | 180 W | | |

### A.3 Shared tenancy

This box is **not dedicated to this project**. A second project (`isv`, developed in `~/cv`) and a
Dify stack run on the same machine and compete for the same VRAM, RAM and ports. Any performance
number in this document is therefore a *shared-machine* number, and re-measuring at a different time
of day can move it. This is a property of the measurement, not noise to be averaged away.

Port allocation reflects this: every Sparkline service is mapped into a `1xxxx` block on the host to
avoid colliding with the other tenant.

| Service | Host port | In-container port |
|---|---|---|
| API (`sparkline_api`) | 18000 | 8000 |
| Open WebUI (`sparkline_webui`) | 13000 | 8080 |
| PostgreSQL (`sparkline_postgres`) | 15432 | 5432 |
| Redis (`sparkline_redis`) | 16379 | 6379 |
| Qdrant (`sparkline_qdrant`) | 16333 / 16334 | 6333 / 6334 |
| MinIO (`sparkline_minio`) | 19000 / 19001 | 9000 / 9001 |
| Ollama — **host service, not the container** | 11434 | — |

### A.4 GPU headroom — the binding constraint

At snapshot, with the LLM loaded and one query in flight:

| Consumer | VRAM | Ours? |
|---|---|---|
| Ollama (`qwen2.5-coder:14b`, host service) | 7472 MiB | yes |
| `sparkline_api` (embedding model on CUDA) | 1488 MiB | yes |
| `isv-app-1` (the other project) | 5758 MiB | **no** |
| Desktop session (Xorg, GNOME, Firefox) | ~460 MiB | no |
| **Total in use** | **15065 MiB of 16311 MiB (92%)** | |
| **Free** | **~1246 MiB** | |

**Our stack accounts for 8960 MiB.** The remaining headroom is roughly 1.2 GB. This is the single
hardest limit on the project right now: moving the reranker to GPU, adopting the larger 20B model,
or raising batch sizes all need VRAM that is not currently free while the other tenant is running.
Track this row carefully across pilots — a change here can invalidate a latency comparison entirely.

---

## Part B — Our system

This is the part that should change between pilots. It is the substance of the comparison.

### B.1 Application stack

| Component | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| API image size | 20.8 GB | | |
| Python | 3.11.15 | | |
| PyTorch | 2.13.0+cu130 (CUDA 13.0, GPU visible) | | |
| FastAPI | 0.111.1 | | |
| sentence-transformers | 3.4.1 | | |
| transformers | 4.57.6 | | |
| qdrant-client | 1.18.0 | | |
| rank-bm25 | 0.2.2 | | |
| pydantic | 2.13.4 | | |
| SQLAlchemy | 2.0.51 | | |
| PostgreSQL | 16-alpine | | |
| Redis | 7-alpine | | |
| Qdrant | latest | | |
| MinIO | latest | | |
| Open WebUI | ghcr.io/open-webui/open-webui:main | | |

### B.2 Models

| Role | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| LLM | `qwen2.5-coder:14b` — 14.8B params, Q4_K_M, 9.0 GB | | |
| LLM serving | Ollama, **host-native** (GPU-attached) via `host.docker.internal:11434` | | |
| LLM device | GPU | | |
| Embedding | `BAAI/bge-large-en` — 1024-dim | | |
| Embedding device | **CUDA** | | |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | | |
| Reranker device | **CPU** | | |
| Max tokens / temperature | 4096 / 0.1 | | |
| LLM timeout | 120 s | | |

Two notes that matter for a fair pilot 2 comparison:

- `hf.co/mradermacher/GPT-OSS-20B-i1-GGUF:Q4_K_M` (20.9B, 15.8 GB) is also pulled on the host and is
  the *default in `config/settings.py`*, but it is **not** what pilot 1 ran. The live `.env` selects
  `qwen2.5-coder:14b`. If pilot 2 switches to the 20B model, that is a model change, not a
  system improvement — record it as such.
- The reranker is still on CPU. It is small (~0.1 GB) and moving it is a cheap, obvious pilot 2 win,
  subject to [A.4](#a4-gpu-headroom--the-binding-constraint).

### B.3 Retrieval and routing configuration

Live values, as read from `.env` at snapshot. Where the running value differs from the committed
default in `config/settings.py`, both are shown — the discrepancy is itself a tracked item.

| Setting | Pilot 1 live value | Code default | Pilot 2 | Pilot 3 |
|---|---|---|---|---|
| `RETRIEVAL_TOP_K_DENSE` | 20 | 20 | | |
| `RETRIEVAL_TOP_K_BM25` | 20 | 20 | | |
| `RETRIEVAL_TOP_K_FUSION` | 20 | 20 | | |
| `RETRIEVAL_TOP_K_RERANK` | 5 | 5 | | |
| `RETRIEVAL_RRF_K` | 60 | 60 | | |
| `CHUNK_SIZE_TOKENS` | 400 | 400 | | |
| `CHUNK_OVERLAP_TOKENS` | 80 | 80 | | |
| `INGEST_MAX_CHUNKS_PER_DOCUMENT` | **2000** | 1000 | | |
| `ROUTER_MODE` | evidence | evidence | | |
| `ROUTER_RAG_SCORE_HIGH` | **-6.0** | -2.0 | | |
| `ROUTER_RAG_SCORE_LOW` | **-6.0** | -5.5 | | |
| `ROUTER_ENABLE_GENERAL_FALLBACK` | true | true | | |
| `ROUTER_CONDENSE_FOLLOWUPS` | true | true | | |
| `TYPO_CORRECTION_ENABLED` | true | true | | |
| `TYPO_MIN_TOKEN_LENGTH` | 4 | 4 | | |
| `TYPO_MAX_EDIT_DISTANCE` | 2 | 2 | | |
| `TYPO_PHONETIC_ENABLED` | true | true | | |
| `TYPO_PROTECT_DICTIONARY_WORDS` | true | true | | |
| `TYPO_SEMANTIC_REWRITE_ENABLED` | false | false | | |
| `JWT_EXPIRE_MINUTES` | 60 | 60 | | |
| `REDIS_SESSION_TTL_SECONDS` | 3600 | 3600 | | |

Pilot 1 shipped with `HIGH` and `LOW` **collapsed to the same value (-6.0)**. That removes the
blended band entirely: a query either scores above -6.0 and is answered strictly from documents, or
falls below and is answered from general knowledge. There is no middle mode in pilot 1, despite the
code supporting one. See [D.2](#d2-router-thresholds-are-not-at-their-calibrated-values).

### B.4 Corpus and data

| Metric | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| Documents ingested | 2 | | |
| Active chunks (retrievable) | 93 | | |
| Qdrant points (incl. superseded versions) | 186 | | |
| Vector dimensions / distance | 1024 / Cosine | | |
| Qdrant collection status | green, 8 segments | | |
| Document versions stored | 4 (2 current, 2 superseded) | | |
| Audit log entries | 508 | | |

Ingested documents at snapshot:

| Document | Type | Size | Chunks |
|---|---|---|---|
| `project work split.docx` | DOCX | 19,728 B | 48 |
| `Sidharth_AI_Assistant_Design.docx` | DOCX | 13,346 B | 45 |

The gap between 93 active chunks and 186 stored points is **by design, not drift**: superseded
document versions keep their vectors and are excluded at query time via `is_active_version`. If
pilot 2 shows points growing faster than chunks, that is expected with each re-upload — it only
becomes a problem if the ratio grows without corresponding re-ingestions.

**The corpus is very small (93 chunks).** Every retrieval-quality number in this document is
measured against it, and the router thresholds in B.3 are specific to it. A larger pilot 2 corpus
will change scores on its own, independent of any improvement. Re-calibrate before comparing.

### B.5 Access and users

| Metric | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| Total accounts | 8 | | |
| Pilot testers | 6 | | |
| Project owner account | 1 (`sidharth.vijayan@`) | | |
| Admin accounts | 1 (`fileadmin@sparkline.in`) | | |
| Departments assigned | 0 — all null | | |
| Designations assigned | 0 — all null | | |
| Per-user document restriction | **not active** | | |
| Auth model | JWT; pipeline uses a service token, never user passwords | | |

Pilot 1 has **no working access control in practice**. The machinery exists — documents carry
`allowed_departments` / `allowed_designations`, and the retrieval layer filters on them — but every
user row has null department and designation, so every tester sees every document. This is the
largest functional gap in the release and the clearest candidate for a pilot 2 improvement.

### B.6 Codebase

| Metric | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| Python files | 64 | | |
| Lines of Python | 10,219 | | |
| Commits on `main` | 137 | | |
| API endpoints | 16 | | |
| Unit tests | **75 passing** | | |
| Accepted upload formats | 9 — pdf, docx, doc, xlsx, xls, xlsm, txt, csv, md | | |

---

## Part C — Measured behaviour

These are the numbers that show whether the system got *better*, as opposed to merely different.
All measured on the pilot 1 corpus (93 chunks), on the shared machine, with the other tenant active.

### C.1 Latency, end to end

Single unwarmed samples through `POST /v1/chat/completions`, non-streaming, measured from the host.
Not averaged — treat as indicative magnitudes, and use the same three question shapes in pilot 2.

| Query shape | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| Health check (`GET /health`) | 0.003 s | | |
| Document-grounded question | **31.8 s** | | |
| General-knowledge question | **12.7 s** | | |
| Misspelled / ambiguous question | 17.7 s | | |

**31.8 seconds for a document question is the headline weakness of pilot 1.** It is the number most
worth attacking, and the one testers are most likely to complain about.

### C.2 Retrieval quality — router calibration

From `python -m eval.calibrate_router`, the project's own harness. Scores are raw cross-encoder
logits (roughly -11 to +11), corpus-specific, and must be re-measured whenever the corpus changes.

| Metric | Pilot 1 (2026-08-14) | Pilot 2 | Pilot 3 |
|---|---|---|---|
| In-corpus questions (n=12) — min | -3.670 | | |
| In-corpus questions — median | 5.893 | | |
| In-corpus questions — max | 9.088 | | |
| Typo'd in-corpus (n=12) — min | -3.670 | | |
| Typo'd in-corpus — median | 5.893 | | |
| General questions (n=15) — min | -11.308 | | |
| General questions — median | -9.760 | | |
| General questions — max | -6.352 | | |
| **Separation gap** | **2.682** | | |
| Typo-tolerance gate | **PASS** | | |

Two results worth keeping in view:

- **Typo tolerance is genuinely working.** All 12 misspelled in-corpus questions score *identically*
  to their correctly spelled versions — the distributions are the same to three decimals. The
  correction happens before retrieval, so the misspelling never reaches the index.
- **Separation is clean but narrow.** 2.682 logits between the worst in-corpus question (-3.670) and
  the best general question (-6.352). It works, but there is little margin; a larger or more varied
  pilot 2 corpus could close it. Widening this gap is a legitimate improvement to target.

### C.3 Runtime footprint at idle

| Container | CPU | Memory |
|---|---|---|
| `sparkline_api` | 0.56% | 1.637 GiB |
| `sparkline_webui` | 0.37% | 610.9 MiB |
| `sparkline_minio` | 0.03% | 69.3 MiB |
| `sparkline_qdrant` | 0.33% | 46.2 MiB |
| `sparkline_postgres` | 0.00% | 39.6 MiB |
| `sparkline_redis` | 0.34% | 4.7 MiB |

System RAM at snapshot: 17 GiB used of 31 GiB, and **swap fully consumed (8.0 GiB of 8.0 GiB)**.
Full swap on a machine with 13 GiB nominally available suggests sustained memory pressure from the
combined tenancy. Worth watching in pilot 2 rather than acting on now.

---

## Part D — Known gaps carried into pilot 1

Stated plainly so that pilot 2 can be credited for closing them. These are things the release ships
*with*, all known and deliberate.

### D.1 Access control is inert
Every user has null department and designation, so document filtering never restricts anyone. See
[B.5](#b5-access-and-users).

### D.2 Router thresholds are not at their calibrated values
Pilot 1 runs `HIGH = LOW = -6.0`. The calibration harness, run at snapshot, recommends
`HIGH = -4.58`, `LOW = -5.47`. The shipped values are deliberately permissive — they send more
queries to the documents and collapse the blended band — but they are not what the measurement
supports. Either adopt the calibrated values in pilot 2 or record why not.

### D.3 Enterprise integration is not wired
The ERP / HRMS / CRM integration is a contract only — `agents/enterprise_agent_interface.py` plus
`ENTERPRISE_ROUTING_CONTRACT.md`. There is no MCP client, and the router has no enterprise branch.
This was gated on Dhruv's servers rather than half-built, which was the right call, but it means
pilot 1 answers questions about **documents and general knowledge only** — no structured business
data.

### D.4 Reranker still on CPU
Cheap win, blocked on VRAM. See [A.4](#a4-gpu-headroom--the-binding-constraint).

### D.5 Ingestion throughput not re-measured since the GPU move
The 1000-chunk cap in `config/settings.py` was sized against **CPU** embedding at roughly 3 chunks
per second. Embedding now runs on CUDA and the live cap was raised to 2000, but the actual GPU
throughput was never measured, so the new cap is not evidence-based. Measure it in pilot 2 and set
the cap from the measurement.

### D.6 Old credentials remain in git history
The former shared password was removed from the code on 2026-08-13 and the pipeline now uses a
service token. The old value is still present in git history. It grants nothing, so this is not
urgent, but it is unresolved and would need a history rewrite to clear.

### D.7 Disk at 82%
163 GB free. Not a problem today; the 20.8 GB API image means a few rebuilds plus model pulls could
make it one.

---

## How to regenerate these numbers

Run these for pilot 2 so the comparison is like-for-like. **Record the corpus size alongside every
retrieval number** — most of them are meaningless without it.

```bash
# ── Part A: hardware ──────────────────────────────────────────────
hostname; lsb_release -a; uname -r
lscpu | grep -E "^(Model name|Core|Thread|CPU\(s\)|CPU max)"
free -h; swapon --show; df -h /
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,compute_cap --format=csv
nvidia-smi --query-compute-apps=pid,used_memory --format=csv   # then attribute each PID:
#   cat /proc/<pid>/cgroup   →   docker inspect --format '{{.Name}}' <container-id>

# ── Part B: stack, models, config ─────────────────────────────────
cd ~/proj1/sparkline-assistant
git log --oneline -1; git rev-list --count HEAD
docker images sparkline-assistant-api --format "{{.Size}}"
docker exec sparkline_api python -c "import sys,torch,importlib.metadata as md; \
print(sys.version.split()[0], torch.__version__, torch.cuda.is_available()); \
[print(p, md.version(p)) for p in ['fastapi','sentence-transformers','transformers','qdrant-client','pydantic','sqlalchemy']]"
curl -s http://localhost:11434/api/tags | python3 -m json.tool     # host Ollama models
grep -vE "^\s*#|^\s*$" .env | sed -E 's/(PASSWORD|SECRET|TOKEN|API_KEY|KEY)=.*/\1=<redacted>/I'

# ── Part B.4/B.5: corpus and users ────────────────────────────────
curl -s http://localhost:16333/collections/sparkline_documents | python3 -m json.tool
docker exec sparkline_postgres psql -U sparkline -d sparkline_db -c \
  "select 'users='||count(*) from users union all \
   select 'documents='||count(*) from documents union all \
   select 'chunks='||count(*) from chunks union all \
   select 'audit='||count(*) from audit_log;"

# ── Part B.6: codebase ────────────────────────────────────────────
find . -name "*.py" -not -path "./.git/*" -not -path "*/__pycache__/*" | wc -l
find . -name "*.py" -not -path "./.git/*" -not -path "*/__pycache__/*" -exec cat {} + | wc -l
docker exec sparkline_api python -m pytest tests/test_unit_components.py -q | tail -3

# ── Part C.2: retrieval quality (the important one) ───────────────
docker exec sparkline_api python -m eval.calibrate_router 2>&1 | tail -12

# ── Part C.3: footprint ───────────────────────────────────────────
docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" \
  sparkline_api sparkline_postgres sparkline_redis sparkline_qdrant sparkline_minio sparkline_webui
```

For **C.1 latency**, mint a session with the service token and time the same three question shapes:

```bash
ST=$(grep '^SERVICE_TOKEN=' .env | cut -d= -f2-)
TOK=$(curl -s -X POST http://localhost:18000/auth/service-token \
  -H 'Content-Type: application/json' -H "X-Service-Token: $ST" \
  -d '{"email":"sidharth.vijayan@sparkline.co.in"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w '%{time_total}s\n' -X POST http://localhost:18000/v1/chat/completions \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"model":"sparkline","messages":[{"role":"user","content":"<question>"}],"stream":false}'
```

The three pilot 1 question shapes were: a document-grounded question about `project work split.docx`,
`"What is the capital of France?"`, and a deliberately misspelled variant of the document question.
