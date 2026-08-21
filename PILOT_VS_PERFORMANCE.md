# Pilot vs Performance — specifications and measured behaviour, by pilot

Renamed from `PILOT_1_BASELINE.md` on 2026-08-21: it now spans every pilot, not only the first.

| | Pilot 1 | Pilot 2 |
|---|---|---|
| **Snapshot taken** | 2026-08-14 | 2026-08-21 |
| **Repository state** | `main` @ `baef833`, 137 commits | `main` @ `2f9a397`, 195 commits |
| **Host** | SEPL-PC | SEPL-PC |
| **Corpus** | 2 internal design documents, 93 chunks | 8 crane/hoist product catalogues, 2857 chunks |

Pilot 1's figures were measured on 2026-08-14; the release to the 6 testers was subsequently moved
to the week of 2026-08-17, so that column is the state of the system immediately *before* pilot 1
rather than on its first day of use. The measured values are unchanged.

## What this file is for

This makes the work done between releases **measurable** rather than remembered: re-run the commands
in [How to regenerate these numbers](#how-to-regenerate-these-numbers), fill the next column, and
the improvement is visible without argument.

Three rules keep it useful:

1. **Never edit the Pilot 1 column.** If a number there turns out to have been measured wrongly, add
   a footnote — do not correct it. A baseline that gets retouched cannot prove anything.
2. **Measure each pilot the same way, on the same corpus, or say plainly that you did not.** Most
   metrics below are corpus-dependent. A latency win measured on a different document set is not a
   win. **Pilot 2 changed the corpus completely** (2 documents → 8, 93 chunks → 2857), so every
   retrieval-quality number below is measured against a different world than pilot 1's. Read those
   rows as *"what the system does now"*, never as *"how much better it got"*.
3. **Record the GPU tenancy state alongside every timing.** Three tenants share one 16 GB card
   ([A.3](#a3-shared-tenancy)). A latency figure without the contemporaneous
   `nvidia-smi` reading is not a measurement, it is an anecdote — and pilot 2 proves it: the same
   question took **31.6 s cold and 15.0 s warm within the same five minutes**.

Empty columns are intentional. Add `Pilot 3`, `Pilot 4` … as further releases land.

### The one-line summary of pilot 2

The corpus went from a 93-chunk toy to a 2857-chunk real one and the system held: grounded answers
with correct citations, verified numbers, general questions correctly refused documents. **No
performance improvement was attempted or achieved** — document latency is unchanged at ~31 s cold,
and the binding constraint is the same as pilot 1's, only worse: `qwen2.5:14b` now runs at
**33% CPU / 67% GPU** because the card is 94% full. See [A.4](#a4-gpu-headroom--the-binding-constraint).

---

## Part A — The GPU server

Hardware is expected to stay fixed across pilots. It is recorded because it is the ceiling every
software number below is measured against, and because the headroom finding in
[A.4](#a4-gpu-headroom--the-binding-constraint) is currently the tightest constraint on the project.

### A.1 Machine

| Specification | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| Hostname | SEPL-PC | SEPL-PC | |
| OS | Ubuntu 24.04.4 LTS (noble) | Ubuntu 24.04.4 LTS (noble) | |
| Kernel | 7.0.0-28-generic | 7.0.0-28-generic | |
| CPU | Intel Core i7-14700K | Intel Core i7-14700K | |
| Cores / threads | 20 cores / 28 threads | 20 cores / 28 threads | |
| CPU max clock | 5.60 GHz | 5.60 GHz | |
| System RAM | 31 GiB | 31 GiB | |
| Swap | 8.0 GiB | 8.0 GiB | |
| Root disk | 915 GB NVMe (`/dev/nvme0n1p2`) | 915 GB NVMe (`/dev/nvme0n1p2`) | |
| Disk free | 163 GB free of 915 GB (82% used) | **141 GB free of 915 GB (84% used)** | |

Hardware is unchanged. **Disk fell 163 GB → 141 GB** across the week; the 8 ingested catalogues are
~102 MB of originals in MinIO, so the bulk of the 22 GB is image layers and model pulls, not corpus.
At 84% with a 20.8 GB API image, a couple of rebuilds is all the remaining headroom — see
[D.7](#d7-disk-at-82).

### A.2 GPU

| Specification | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti | NVIDIA GeForce RTX 5060 Ti | |
| VRAM | 16311 MiB (16 GB) | 16311 MiB (16 GB) | |
| Driver | 595.84 | 595.84 | |
| CUDA (driver) | 13.2 | 13.2 | |
| Compute capability | 12.0 | 12.0 | |
| Power cap | 180 W | 180 W | |

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

| Consumer | Pilot 1 VRAM | Pilot 2 VRAM | Ours? |
|---|---|---|---|
| Ollama (host service) | 7472 MiB (`qwen2.5-coder:14b`) | 6790 MiB (`qwen2.5:14b`) | yes |
| `sparkline_api` (embedding + reranker) | 1488 MiB | 1508 MiB | yes |
| Suyash's CV stack (`isv`, host python) | 5758 MiB | 6762 MiB | **no** |
| Desktop session (Xorg, GNOME, Firefox) | ~460 MiB | ~277 MiB | no |
| **Total in use** | **15065 MiB of 16311 MiB (92%)** | **15337 MiB of 16311 MiB (94%)** | |
| **Free** | **~1246 MiB** | **~974 MiB** | |
| **Ours** | **8960 MiB** | **8298 MiB** | |

**Pilot 2 is tighter than pilot 1, and it now visibly hurts.** Headroom fell 1246 → 974 MiB because
the other tenant grew by 1 GB. The consequence is measurable rather than theoretical:

    $ ollama ps
    NAME           SIZE     PROCESSOR          CONTEXT
    qwen2.5:14b    10 GB    33%/67% CPU/GPU    4096

**The LLM no longer fits.** A third of every token is generated on the CPU. That is the direct cause
of the [C.1](#c1-latency-end-to-end) figures, and it is why pilot 2 shows no latency improvement
despite no software regression: the same code on an uncontended card would be materially faster.

This remains the single hardest limit on the project. Moving the reranker to GPU, a larger model, or
bigger batches all need VRAM that is not free while the other tenant runs. **Coordinating with
Suyash buys more than any code change available today.** Track this table carefully — a change here
can invalidate a latency comparison entirely.

---

## Part B — Our system

This is the part that should change between pilots. It is the substance of the comparison.

### B.1 Application stack

| Component | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| API image size | 20.8 GB | 20.8 GB | |
| Python | 3.11.15 | 3.11.15 | |
| PyTorch | 2.13.0+cu130 (CUDA 13.0, GPU visible) | 2.13.0+cu130 (CUDA 13.0, GPU visible) | |
| FastAPI | 0.111.1 | 0.111.1 | |
| sentence-transformers | 3.4.1 | 3.4.1 | |
| transformers | 4.57.6 | 4.57.6 | |
| qdrant-client | 1.18.0 | 1.18.0 | |
| rank-bm25 | 0.2.2 | 0.2.2 | |
| pydantic | 2.13.4 | 2.13.4 | |
| SQLAlchemy | 2.0.51 | 2.0.51 | |
| PostgreSQL | 16-alpine | 16-alpine | |
| Redis | 7-alpine | 7-alpine | |
| Qdrant | latest | latest | |
| MinIO | latest | latest | |
| Open WebUI | ghcr.io/open-webui/open-webui:main | ghcr.io/open-webui/open-webui:main | |
| Docker / Compose | — (not recorded) | 29.5.2 / v5.1.4 | |
| Ollama (host) | — (not recorded) | 0.18.2 | |
| Tesseract OCR (in image) | — (not recorded) | 5.5.0, leptonica 1.84.1 | |

**The stack is byte-for-byte identical to pilot 1.** No dependency was upgraded, so nothing in this
table can explain any difference in behaviour — which is exactly what makes the corpus and GPU
contention the only candidates. Docker, Ollama and Tesseract were not recorded for pilot 1; they are
added here because Tesseract in particular gates whether a scanned PDF ingests at all.

### B.2 Models

| Role | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| LLM | `qwen2.5-coder:14b` — 14.8B params, Q4_K_M, 9.0 GB | **`qwen2.5:14b`** — Q4_K_M, 9.0 GB | |
| LLM serving | Ollama, **host-native** (GPU-attached) via `host.docker.internal:11434` | unchanged | |
| LLM device | GPU | **33% CPU / 67% GPU — does not fit** | |
| LLM context window | — (not recorded) | 4096 | |
| Embedding | `BAAI/bge-large-en` — 1024-dim | `BAAI/bge-large-en` — 1024-dim | |
| Embedding device | **CUDA** | **CUDA** (verified `torch.cuda.is_available()`) | |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | |
| Reranker device | **CPU** | **CPU** — still | |
| Max tokens / temperature | 4096 / 0.1 | 4096 / 0.1 | |
| LLM timeout | 120 s | 120 s | |
| Models resident on host Ollama | 3 (incl. GPT-OSS 20B) | **2** — `qwen2.5:14b`, `qwen2.5-coder:14b` | |

Notes for a fair comparison:

- **The LLM changed between pilots.** Pilot 1 ran `qwen2.5-coder:14b`; pilot 2 runs `qwen2.5:14b`,
  swapped on 2026-08-18 because the coder-tuned model is weaker at policy and HR prose. Same size and
  quantisation, so VRAM is comparable, but **any answer-quality difference may be the model, not the
  system.** Record it as a model change.
- **`qwen2.5-coder:14b` must stay installed.** The shared company Dify instance is registered against
  it on this same host Ollama daemon. It is company infrastructure used by several people including
  the tech head — deleting it breaks them. Because that second model is therefore permanent,
  `OLLAMA_MAX_LOADED_MODELS=1` is the *entire* VRAM mitigation, not half of it.
- **GPT-OSS 20B was deleted on 2026-08-18** and must not be re-evaluated: tested 2026-08-07, it
  returned empty responses despite fitting in VRAM. It is no longer the code default.
- The reranker is **still on CPU** — deliberately, since VRAM is shared and there is under 1 GB free.
  It remains the cheapest available win the moment headroom exists.

### B.3 Retrieval and routing configuration

Live values, as read from `.env` at snapshot. Where the running value differs from the committed
default in `config/settings.py`, both are shown — the discrepancy is itself a tracked item.

| Setting | Pilot 1 live value | Code default | Pilot 2 live value | Pilot 3 |
|---|---|---|---|---|
| `RETRIEVAL_TOP_K_DENSE` | 20 | 20 | 20 | |
| `RETRIEVAL_TOP_K_BM25` | 20 | 20 | 20 | |
| `RETRIEVAL_TOP_K_FUSION` | 20 | 20 | 20 | |
| `RETRIEVAL_TOP_K_RERANK` | 5 | 5 | 5 | |
| `RETRIEVAL_RRF_K` | 60 | 60 | 60 | |
| `CHUNK_SIZE_TOKENS` | 400 | 400 | 400 | |
| `CHUNK_OVERLAP_TOKENS` | 80 | 80 | 80 | |
| `INGEST_MAX_CHUNKS_PER_DOCUMENT` | **2000** | 1000 | **2000** | |
| `ROUTER_MODE` | evidence | evidence | evidence | |
| `ROUTER_RAG_SCORE_HIGH` | **-6.0** | -2.0 | **-6.0** — unchanged | |
| `ROUTER_RAG_SCORE_LOW` | **-6.0** | -5.5 | **-6.0** — unchanged | |
| `ROUTER_ENABLE_GENERAL_FALLBACK` | true | true | true | |
| `ROUTER_CONDENSE_FOLLOWUPS` | true | true | true | |
| `TYPO_CORRECTION_ENABLED` | true | true | true | |
| `TYPO_MIN_TOKEN_LENGTH` | 4 | 4 | 4 | |
| `TYPO_MAX_EDIT_DISTANCE` | 2 | 2 | 2 | |
| `TYPO_PHONETIC_ENABLED` | true | true | true | |
| `TYPO_PROTECT_DICTIONARY_WORDS` | true | true | true | |
| `TYPO_SEMANTIC_REWRITE_ENABLED` | false | false | false | |
| `JWT_EXPIRE_MINUTES` | 60 | 60 | 60 | |
| `REDIS_SESSION_TTL_SECONDS` | 3600 | 3600 | 3600 | |

**Nothing in this table changed between pilots.** Retrieval and routing are configured identically,
which matters: it means pilot 2's behaviour differences come from the corpus, not from tuning.

Both pilots ship with `HIGH` and `LOW` **collapsed to the same value (-6.0)**. That removes the
blended band entirely: a query either scores above -6.0 and is answered strictly from documents, or
falls below and is answered from general knowledge. There is no middle mode in either pilot, despite
the code supporting one. See [D.2](#d2-router-thresholds-are-not-at-their-calibrated-values).

**The -6.0 floor is now carrying far less margin than it was.** It was chosen against a corpus whose
general questions scored -11.3 to -6.35. Against the pilot 2 corpus, a mid-conversation topic switch
(`by the way what is 2 + 2`) scored **1.66** and was answered from documents. Standalone general
questions still route correctly — verified live — but the margin is no longer comfortable. This is
the strongest argument for recalibrating before pilot 3.

### B.4 Corpus and data

| Metric | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| Documents ingested | 2 | **10** (2 design docs + 8 catalogues) | |
| Active chunks (retrievable) | 93 | **2857** | |
| Qdrant points | 186 | **2857** | |
| BM25 corpus size | — (not recorded) | 2764 | |
| Vector dimensions / distance | 1024 / Cosine | 1024 / Cosine | |
| Qdrant collection status | green, 8 segments | green, 8 segments | |
| Document versions stored | 4 (2 current, 2 superseded) | 12 | |
| Session-document points (`sparkline_session_docs`) | — (not recorded) | **0** — per-chat upload is off | |
| Audit log entries | 508 | 941 | |

Ingested documents at pilot 2 snapshot — the tester corpus, all `--public` and **untagged**
(HR had not supplied departments; label later with `ingest_cli set-access`):

| Document | Type | Size | Pages | Chunks | chunks/page | needs_ocr | avg tokens |
|---|---|---|---|---|---|---|---|
| `Wire rope hoists  Product information_NEW.pdf` | PDF | 7.2 MB | 216 | 899 | 4.2 | 3 | 362.8 |
| `End Carriages_2020-02-1.pdf` | PDF | 6.5 MB | 144 | 520 | 3.6 | 3 | 359.3 |
| `DEMAG.pdf` | PDF | 11.3 MB | 180 | 361 | 2.0 | 0 | 319.8 |
| `Chain hoists  Product information.pdf` | PDF | 5.8 MB | 96 | 327 | 3.4 | 0 | 348.5 |
| `eepos-Catalogue-2019.pdf` | PDF | 15.5 MB | 156 | 294 | 1.9 | 4 | 319.3 |
| `LR COMPONANT Manuals.pdf` | PDF | 37.8 MB | 81 | 97 | 1.2 | 2 | 279.5 |
| `FHBR-011223-Finrae-catalog-update-2024.pdf` | PDF | 14.5 MB | 104 | 93 | **0.9** | 4 | **199.1** |
| `handtakels-yale_0.pdf` | PDF | 3.2 MB | 44 | 80 | 1.8 | 0 | 327.8 |
| `project work split.docx` (from pilot 1) | DOCX | 19,728 B | — | 48 | — | — | — |
| `Sidharth_AI_Assistant_Design.docx` (from pilot 1) | DOCX | 13,346 B | — | 45 | — | — | — |

Three findings worth carrying forward:

- **`needs_ocr` is 0–4 on every file**, so these are native-text PDFs rather than scans. No Tesseract
  guesswork to distrust in any answer — which is why numeric spot-checks verified cleanly.
- **File size does not predict text volume.** `LR COMPONANT Manuals.pdf` is the largest file at
  37.8 MB and produced the third-*fewest* chunks (97 from 81 pages). The bytes are images sitting
  alongside the text, and images are discarded at parse time.
- **`FHBR-011223-Finrae` is the weak document**: 0.9 chunks/page and 199 avg tokens against 320–360
  elsewhere. Its pages are genuinely thin, so most of that catalogue lives in graphics the parsers
  drop. Expect poor answers on Finrae products specifically.

Points (2857) now equal active chunks (2857), where pilot 1 had 186 points against 93 active chunks.
Pilot 1's 2:1 ratio was superseded versions retained and excluded at query time via
`is_active_version` — by design, not drift. Pilot 2's fresh uploads created no superseded versions,
so the ratio collapsed to 1:1. The 93-chunk gap against BM25's 2764 is chunks with **no indexable
tokens** — a page number or a bare caption.

**The corpus changed completely, so no retrieval-quality number here is comparable to pilot 1.**
The router thresholds in B.3 were measured against 93 chunks of the project's own design documents
and were **not** re-measured for pilot 2. That is the top outstanding job — see
[D.2](#d2-router-thresholds-are-not-at-their-calibrated-values).

### B.5 Access and users

| Metric | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| Total accounts (Postgres) | 8 | 8 | |
| Total accounts (`webui.db`) | — (not recorded) | 8, emails matching | |
| Pilot testers | 6 | 6 | |
| Project owner account | 1 (`sidharth.vijayan@`) | 1 (`sidharth.vijayan@`) | |
| Admin accounts | 1 (`fileadmin@sparkline.in`) | 1 (`file.admin`) | |
| Model access grants for the `sparkline` pipe | — (not recorded) | 7 per-user rows | |
| Departments assigned | 0 — all null | **0 — all null** | |
| Designations assigned | 0 — all null | **0 — all null** | |
| Per-user document restriction | **not active** | **not active** | |
| Raw Ollama models visible to testers | — (not recorded) | **0** — both `is_active=0` | |
| Signup / web search | — (not recorded) | both disabled | |
| Auth model | JWT; pipeline uses a service token, never user passwords | unchanged | |
| Tester password scheme | shared default | **per-user** (`firstname@2026`), set in `webui.db` | |

**Access control is still inert in pilot 2** — unchanged, and for the same reason. The machinery
exists (documents carry `allowed_departments` / `allowed_designations`, and the retrieval layer
filters on them) but every user row has null department and designation, so every tester sees every
document. This is gated on **HR supplying department and designation data**, not on code. It remains
the largest functional gap.

What *did* change: since the 2026-08-13 auth rework the two password sets are independent — Open
WebUI passwords are what testers log in with, and the API never sees them. Pilot 2 testers have
individual passwords rather than a shared one. Passwords are bcrypt hashes only and are **not
recoverable**; reset them, do not try to read them back.

### B.6 Codebase

| Metric | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| Python files | 64 | **93** | |
| Lines of Python | 10,219 | **16,437** | |
| Commits on `main` | 137 | **195** | |
| API endpoints | 16 | **28** | |
| Unit tests | **75 passing** | **230 passing** (~6 s) | |
| Live precommit checks | — (not recorded) | 20 passed, 2 failed, 5 human reads | |
| Adversarial (injection) checks | — (not recorded) | **17 passed, 0 failed**, 3 human reads | |
| Accepted upload formats | 9 — pdf, docx, doc, xlsx, xls, xlsm, txt, csv, md | 10 — adds `.tsv`; also `.log` for text | |

**Tests tripled (75 → 230) while the codebase grew 60%.** The two evaluation harnesses did not exist
at pilot 1 and are the more meaningful addition: `eval.precommit_checks` exercises routing, refusal
and the audit trail against the live stack, and `eval.adversarial_checks` covers six areas of prompt
injection. The latter went from 11 passed / 4 failed to 18/0 on 2026-08-19 when document-borne
injection and system-prompt disclosure were both closed.

The two precommit failures are known and deliberate: the degenerate routing band
([D.2](#d2-router-thresholds-are-not-at-their-calibrated-values)) and the mid-conversation topic
switch described in [B.3](#b3-retrieval-and-routing-configuration). The five human reads are
**by design** — assertions that cannot be made mechanically. Do not convert them to asserts.

---

## Part C — Measured behaviour

These are the numbers that show whether the system got *better*, as opposed to merely different.
All measured on the pilot 1 corpus (93 chunks), on the shared machine, with the other tenant active.

### C.1 Latency, end to end

Single unwarmed samples through `POST /v1/chat/completions`, non-streaming, measured from the host.
Not averaged — treat as indicative magnitudes, and use the same three question shapes each pilot.

| Query shape | Pilot 1 (2026-08-14) | Pilot 2 cold (2026-08-21) | Pilot 2 **warm** | Pilot 3 |
|---|---|---|---|---|
| Health check (`GET /health`) | 0.003 s | 0.002 s | — | |
| Document-grounded question | **31.8 s** | **31.6 s** | **15.0 s** | |
| General-knowledge question | **12.7 s** | **34.5 s** | **2.4 s** | |
| Misspelled / ambiguous question | 17.7 s | 28.7 s | 11.0 s | |

**Read the cold column, not the warm one, when comparing to pilot 1** — pilot 1's method was a single
unwarmed sample, so cold is the like-for-like figure. On that basis a document question is
**unchanged: 31.8 s → 31.6 s**, on a corpus 30× larger. No improvement, but no regression from scale
either, which is the more useful reading.

**The cold/warm spread is the real finding, and it is enormous.** The same three questions, minutes
apart on the same build:

    cold   31.6 s   34.5 s   28.7 s      (no model resident — every call pays a ~10 GB load)
    warm   15.0 s    2.4 s   11.0 s      (model resident, back-to-back)

`OLLAMA_MAX_LOADED_MODELS=1` plus keep-alive expiry means an idle system unloads the model, so the
**first question after a quiet period pays the full load**. A tester asking one question every few
minutes — exactly how a pilot is used — sees the cold numbers, not the warm ones. Quoting only the
warm figures would misrepresent the experience; quoting only cold would understate the system.

Note the cold general-knowledge question at 34.5 s is *slower* than the document question. That is
not a routing cost: the general path skips retrieval entirely (2.4 s warm, the fastest of the three).
It simply drew the model load. **Single samples on a contended GPU cannot be ranked against each
other** — which is the whole reason rule 3 at the top of this file exists.

Contemporaneous GPU state for every figure above: **15337 / 16311 MiB (94%)**, three tenants,
`qwen2.5:14b` running **33% CPU / 67% GPU**. A third of every token generated on the CPU.

### C.2 Retrieval quality — router calibration

From `python -m eval.calibrate_router`, the project's own harness. Scores are raw cross-encoder
logits (roughly -11 to +11), corpus-specific, and must be re-measured whenever the corpus changes.

| Metric | Pilot 1 (2026-08-14) | Pilot 2 (2026-08-21) | Pilot 3 |
|---|---|---|---|
| In-corpus questions (n=12) — min | -3.670 | **not measured** | |
| In-corpus questions — median | 5.893 | **not measured** | |
| In-corpus questions — max | 9.088 | **not measured** | |
| Typo'd in-corpus (n=12) — min | -3.670 | **not measured** | |
| Typo'd in-corpus — median | 5.893 | **not measured** | |
| General questions (n=15) — min | -11.308 | **not measured** | |
| General questions — median | -9.760 | **not measured** | |
| General questions — max | -6.352 | **not measured** | |
| **Separation gap** | **2.682** | **not measured** | |
| Typo-tolerance gate | **PASS** | **not measured** | |

**Pilot 2 shipped without re-calibrating, and this is the largest single gap in the release.** The
reason is deliberate rather than an oversight: `IN_CORPUS` and `IN_CORPUS_TYPOD` in
`eval/calibrate_router.py` still ask about the project's own design documents in those documents'
own vocabulary ("What does the memory manager track instead of raw message history?"). Running the
harness unchanged against 8 crane catalogues would produce numbers that **look** like a calibration
and measure nothing. Rewriting the question sets against the real corpus has to come first, and it
needs someone who knows the content.

What *was* verified live in place of a calibration, as a pilot user through Open WebUI:

| Probe | Result |
|---|---|
| Document question (`what capacity wire rope hoists are available?`) | document answer, correct citation, **numbers verified by hand** |
| Same question misspelled | stayed in the document band, but retrieved **worse** passages and gave a weaker answer |
| `tell me a joke` | general, no citations |
| `what is our leave policy` | general, no citations |
| `who is the CEO of Google` | general, no citations |
| Value from an **image** load chart | returned the *method and formulas*, not the number — the diagram was never parsed |
| Value from a **text** table (aluminium XL profile weights) | correct, verified by hand |
| `by the way what is 2 + 2` **mid-conversation** | **routed to documents at score 1.66** — should have been general |

Two results worth keeping in view from pilot 1, still relevant:

- **Typo tolerance worked perfectly on the pilot 1 corpus.** All 12 misspelled in-corpus questions
  scored *identically* to their correctly spelled versions, because correction happens before
  retrieval. **Pilot 2 weakens this claim:** the misspelled catalogue question still routed to
  documents, but retrieved worse passages and produced a worse answer. Corrections are rebuilt from
  the vocabulary of whatever documents are ingested, so crane part numbers and German-derived product
  names are a harder target than the design docs' prose. Treat "typo tolerance PASS" as unproven for
  pilot 2 until re-measured.
- **Separation was clean but narrow** — 2.682 logits at pilot 1. The 1.66 score above suggests it is
  narrower now, not wider.

Two results worth keeping in view:

- **Typo tolerance is genuinely working.** All 12 misspelled in-corpus questions score *identically*
  to their correctly spelled versions — the distributions are the same to three decimals. The
  correction happens before retrieval, so the misspelling never reaches the index.
- **Separation is clean but narrow.** 2.682 logits between the worst in-corpus question (-3.670) and
  the best general question (-6.352). It works, but there is little margin; a larger or more varied
  pilot 2 corpus could close it. Widening this gap is a legitimate improvement to target.

### C.3 Runtime footprint at idle

| Container | Pilot 1 CPU | Pilot 1 memory | Pilot 2 CPU | Pilot 2 memory |
|---|---|---|---|---|
| `sparkline_api` | 0.56% | 1.637 GiB | 0.40% | **1.244 GiB** |
| `sparkline_webui` | 0.37% | 610.9 MiB | 0.34% | **160.0 MiB** |
| `sparkline_minio` | 0.03% | 69.3 MiB | 0.02% | 96.4 MiB |
| `sparkline_qdrant` | 0.33% | 46.2 MiB | 0.39% | 55.4 MiB |
| `sparkline_postgres` | 0.00% | 39.6 MiB | 0.00% | 36.0 MiB |
| `sparkline_redis` | 0.34% | 4.7 MiB | 0.37% | 5.0 MiB |

System RAM: pilot 1 was 17 GiB used of 31 GiB with **swap fully consumed (8.0 of 8.0 GiB)**.
Pilot 2 is **14 GiB used of 31 GiB, 17 GiB available**, swap still effectively full at 7.8 of 8.0 GiB.

Container memory is *lower* than pilot 1 despite a 30× larger corpus, because both `sparkline_api`
and `sparkline_webui` had been restarted shortly before the reading. Read these as a floor, not a
steady state — `sparkline_api` at pilot 1's 1.637 GiB is the more representative figure for a
long-running process. Qdrant grew only 46 → 55 MiB for 2857 points, since vectors are memory-mapped
rather than resident.

**Swap remains pinned near full**, unchanged from pilot 1, on a machine with 17 GiB nominally
available. That pattern points at sustained pressure from the combined tenancy rather than at our
stack. Still worth watching rather than acting on.

---

## Part D — Known gaps, and what each pilot did about them

Stated plainly so a later pilot can be credited for closing them. These are things the release ships
*with*, all known and deliberate. Each carries its pilot 2 status.

| Gap | Pilot 1 | Pilot 2 |
|---|---|---|
| D.1 Access control inert | open | **still open** — gated on HR data |
| D.2 Router thresholds uncalibrated | open | **still open, and now worse** |
| D.3 Enterprise integration not wired | open | **still open** — gated on Dhruv |
| D.4 Reranker on CPU | open | **still open** — under 1 GB VRAM free |
| D.5 Ingestion throughput unmeasured | open | **partially closed** |
| D.6 Old credentials in git history | open | still open |
| D.7 Disk pressure | 82% | **84%** |
| D.8 Images in documents dropped | not yet identified | **open, confirmed on real content** |
| D.9 Per-chat upload unavailable | n/a | **open, deliberately** |
| D.10 Citation box shows the wrong page | not yet identified | **open** |
| D.11 Mid-chat topic switch stays on documents | not yet identified | **open** |
| D.12 Blended mode unreachable | open (consequence of D.2) | **still open** |

### D.1 Access control is inert
Every user has null department and designation, so document filtering never restricts anyone. See
[B.5](#b5-access-and-users). **Pilot 2: unchanged.** Gated on HR supplying department and designation
data, not on code. All 8 pilot 2 documents were ingested `--public` and untagged for the same reason;
`ingest_cli set-access` exists to label them later without re-uploading.

### D.2 Router thresholds are not at their calibrated values
Pilot 1 runs `HIGH = LOW = -6.0`. The calibration harness recommended `HIGH = -4.58`, `LOW = -5.47`.
**Pilot 2: unchanged at -6.0/-6.0, and the situation deteriorated.** Those pilot 1 recommendations
were measured against 186 points from two documents and are **invalid** now the corpus is 2857 points
of unrelated content. Re-calibration was not run because the question sets must be rewritten first
(see [C.2](#c2-retrieval-quality--router-calibration)). Evidence the floor is now marginal: a
mid-conversation general question scored **1.66** and was answered from documents.
**This is the top job for pilot 3.** `eval/precommit_checks.py` fails on it deliberately — do not
"fix" it by inventing numbers.

### D.3 Enterprise integration is not wired
The ERP / HRMS / CRM integration is a contract only — `agents/enterprise_agent_interface.py` plus
`ENTERPRISE_ROUTING_CONTRACT.md`. There is no MCP client, and the router has no enterprise branch.
This was gated on Dhruv's servers rather than half-built, which was the right call, but it means both
pilots answer questions about **documents and general knowledge only** — no structured business data.
**Pilot 2: unchanged.**

### D.4 Reranker still on CPU
Cheap win, blocked on VRAM. See [A.4](#a4-gpu-headroom--the-binding-constraint).
**Pilot 2: unchanged, and headroom fell from ~1246 to ~974 MiB**, so it moved further out of reach.

### D.5 Ingestion throughput not re-measured since the GPU move
The 1000-chunk cap in `config/settings.py` was sized against **CPU** embedding at roughly 3 chunks
per second; the live cap is 2000. **Pilot 2 supplies the first real GPU evidence**, from the ingestion
log timings: `Wire rope hoists` embedded and indexed **899 chunks in 39 s (~23 chunks/s)**, and
`LR COMPONANT` 97 chunks in 4 s (~24 chunks/s). That is roughly **8× the CPU rate** the cap was sized
against, and no document hit the 2000 cap (largest was 899, no `truncated` flag on any upload). The
cap is now defensible, though sizing it deliberately from this measurement is still open.

### D.6 Old credentials remain in git history
The former shared password was removed from the code on 2026-08-13 and the pipeline now uses a
service token. The old value is still present in git history. It grants nothing, so this is not
urgent, but it is unresolved and would need a history rewrite to clear. **Pilot 2: unchanged.**

### D.7 Disk at 82%
163 GB free at pilot 1. **Pilot 2: 141 GB free, 84% used** — 22 GB consumed in a week. The 20.8 GB
API image means a few rebuilds plus model pulls could make this a real problem. Closer to acting on
than it was.

### D.8 Images in source documents are silently dropped
**New in pilot 2, and the limitation testers are most likely to hit.** An image embedded in a document
is discarded at parse time in **every** supported format, with no error and no reference on the stored
chunk: `docx_parser.py` never walks `w:drawing`/`w:pict`; `excel_parser.py` records a chart's *title*
only; `pdf_parser.py` takes text only. The Qdrant chunk payload has **no field** capable of holding an
image reference, so this is a data-model gap rather than an unwired connection.

Confirmed on real content: asked for a value from a graphical load chart, the assistant returned the
*formulas and method for reading the diagram* — the surrounding prose — because it never saw the
curve. Asked the equivalent question against a real **text** table, it answered correctly. A tester
whose answer lives in a diagram gets the surrounding prose or nothing, and **is never told anything
was omitted.** Shipped as a stated limitation; options and why each is deferred are in
`internship-tracking/FUTURE_SCOPE.md`.

### D.9 Per-chat file upload is unavailable
**New in pilot 2, deliberately.** Verified in the live UI: the `+` menu renders "Upload Files" and
"Attach Files" as greyed-out placeholders. `sparkline_session_docs` holds 0 points. The blocker is
access control, not parsing: `access_control/pep.py:52-53` returns `must=[is_active_version]` for
`full_access=True` users — which every pilot user is — so a session-uploaded document would be visible
to **every** user. Session scoping in the Qdrant filter, a TTL for temporary chunks, and attachment
forwarding in the pipe all exist in design; enabling the toggle on release day was rejected as putting
an isolation path that has never run in production in front of six people at once.

### D.10 The citation box shows one page per document, not the pages cited
**New in pilot 2.** `_dedupe_citations()` in `open_webui_pipeline/sparkline_pipeline.py:539` collapses
citations to one entry per source document, keeping the best-ranked chunk. An answer whose facts come
from p.6 and p.136 therefore displays a Source box reading only p.142. A tester who opens p.142 to
check a number finds nothing and reasonably concludes the answer was invented.

Mitigated but not fixed: commit `2f9a397` made the **inline** citations name the filename and correct
page (they previously read `SOURCE 4, Page 6`, an internal label meaningless to the reader), so
verification is possible from the prose. Deferred because the executing pipeline lives in Open WebUI's
SQLite `function` table, not the repo file — fixing it needs a DB update plus a `sparkline_webui`
restart.

### D.11 A mid-chat topic switch keeps answering from documents
**New in pilot 2.** Follow-up condensing (`ROUTER_CONDENSE_FOLLOWUPS=true`) rewrites a question
against conversation history, so an unrelated question asked after a document question retrieves as
though still on that subject. `by the way what is 2 + 2` scored **1.66** and was answered from
documents. The answer was still correct and honestly labelled, so this ships with the workaround
*"start a new chat when you change topic"*. Standalone general questions route correctly.

### D.12 Blended mode is unreachable
A direct consequence of D.2. `router/query_router.py:212` computes `blended = score < high` only after
`score >= low` has passed, so equal `HIGH` and `LOW` makes `blended` permanently `False`. **No answer
in either pilot marks which parts came from general knowledge versus documents.** Closing D.2 closes
this automatically.

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
# Qdrant needs an API key since the 2026-08-19 hardening, and is loopback-only.
# Asking from inside the container avoids both problems:
docker exec sparkline_api python -c "
from services import qdrant_service as q
c = q._get_client()
i = c.get_collection('sparkline_documents')
print('points', i.points_count, 'status', i.status, 'segments', i.segments_count)
print('session', c.get_collection('sparkline_session_docs').points_count)"

docker exec sparkline_postgres psql -U sparkline -d sparkline_db -c \
  "select 'users='||count(*) from users union all \
   select 'documents='||count(*) from documents union all \
   select 'versions='||count(*) from document_versions union all \
   select 'chunks='||count(*) from chunks union all \
   select 'audit='||count(*) from audit_log;"

# Open WebUI accounts, roles and model grants live in a *separate* SQLite DB:
docker exec sparkline_webui python -c "
import sqlite3
c = sqlite3.connect('/app/backend/data/webui.db')
for r in c.execute('select id, email, role from \"user\" order by role'): print(r)
print('grants', c.execute(\"select count(*) from access_grant where resource_id='sparkline'\").fetchone())"

# Per-document parse quality — the numbers behind the B.4 table:
docker logs sparkline_api 2>&1 | grep "pdf_parser.complete"    # total_pages, needs_ocr
docker logs sparkline_api 2>&1 | grep "chunker.pdf_complete"   # total_chunks, avg_tokens

# ── Part B.6: codebase ────────────────────────────────────────────
find . -name "*.py" -not -path "./.git/*" -not -path "*/__pycache__/*" | wc -l
find . -name "*.py" -not -path "./.git/*" -not -path "*/__pycache__/*" -exec cat {} + | wc -l
# pytest is NOT in the image (Dockerfile runs poetry install --no-dev) and any
# hand-installed copy dies on a container recreate. Restore it first:
docker exec sparkline_api pip install --no-cache-dir \
    "pytest>=8.2" "pytest-asyncio>=0.23" "pytest-httpx>=0.30"
docker exec sparkline_api python -m pytest -q | tail -3

# ── Part C.2: retrieval quality (the important one) ───────────────
# Rewrite IN_CORPUS / IN_CORPUS_TYPOD against the CURRENT corpus BEFORE running
# this, or it measures nothing. Each check is a live LLM call, so both suites
# below take 5-15 min on a contended GPU — run them detached, not in a terminal
# you are watching, and never in parallel with each other.
docker exec sparkline_api python -m eval.calibrate_router    > logs/calibrate_$(date +%d%b).log 2>&1 &
docker exec sparkline_api python -m eval.precommit_checks    > logs/precommit_$(date +%d%b).log 2>&1 &
docker exec sparkline_api python -m eval.adversarial_checks  > logs/adversarial_$(date +%d%b).log 2>&1 &

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
Pilot 2 kept the general-knowledge question identical and substituted a corpus-appropriate document
question (`"What capacity wire rope hoists are available?"`) plus its misspelled variant.

**Measure cold and warm, and label which is which.** Pilot 2 found a 2× spread between them:

```bash
ollama ps                      # empty = cold; a listed model = warm
                               # also shows PROCESSOR, e.g. "33%/67% CPU/GPU" = does NOT fit
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
```

Run the three timings once on a cold daemon (the like-for-like figure against pilot 1), then
immediately again back-to-back for the warm figure. **Record the `nvidia-smi` output alongside them**
— without it the numbers cannot be compared to any other pilot. Attribute each GPU PID:
`/usr/local/bin/ollama` is our LLM, `/usr/local/bin/python3.11` is our embedding + reranker, and
`/usr/bin/python3` is Suyash's CV stack, which is **outside Ollama and invisible to `ollama ps`**.
