# Sparkline Assistant — working notes for Claude

Local LLM + RAG assistant for Sparkline. No external AI APIs. Read this before changing
anything; several parts of the system fail silently in ways the code alone does not reveal.

Written 2026-08-18, when development moved off the shared server onto a laptop over SSH.

---

## Where things actually run

The server is `SEPL-PC` (user `sepl`), and **three unrelated things share it**. Everything runs as
the same `sepl` user, so file ownership will not tell you who owns what:

| Path / containers | Owner | What it is |
|---|---|---|
| `~/proj1/sparkline-assistant`, `sparkline_*` | ours | this project |
| `~/cv` (`isv`, Fire YOLO, PPE, insightface) | **Suyash** | CCTV / person detection. Uses the GPU **directly**, not through Ollama. |
| `/home/sepl/dify`, all `docker-*` containers | **shared company Dify** | Pre-existing team instance (accounts include Kaushik Iyer, Sandeep Pansare, Shruti Doshi, Yash Mehta), dating to March 2026. Not a teammate's dev project. |

Never restart, stop, or reconfigure a `docker-*` container or anything under `~/cv`. Ours are all
named `sparkline_*`.

Dhruv, who owns the enterprise/ERP half of this project, works from his own laptop and has **no
footprint on this server** — no MCP servers, no containers. Do not go looking for his services here.

### The LLM is not in the container

This catches everyone. `sparkline_ollama` is **empty and unused** — it is deliberately parked on
port 21434 so it cannot claim the real one. The actual LLM is a **host** Ollama (systemd,
`/usr/local/bin/ollama`, port 11434) with direct GPU access.

- `.env` sets `LLM_BASE_URL=http://localhost:11434/v1` — correct for running the app natively.
- `docker-compose.server.yml:22` overrides it to `http://host.docker.internal:11434/v1` — correct
  for the containerised API. Both must stay in step.

### Three tenants contend for one 16 GB GPU

- **Us:** ~1.5 GB permanently held by the embedding and reranker models, plus ~9 GB whenever
  Ollama has a 14B model warm.
- **Shared company Dify:** registered against `qwen2.5-coder:14b` on this same host Ollama daemon,
  so `ollama pull` and `ollama rm` are **not** private actions.
- **Suyash's CV work:** YOLO and insightface loaded onto the GPU **directly**, outside Ollama, so
  it is invisible to `ollama ps` and appears only in `nvidia-smi`. His stack is often down — a
  reading of "14 GB free" usually means he is simply not running, not that there is headroom.

Check `nvidia-smi` before assuming VRAM is available, and treat a GPU-heavy run as something to
coordinate with Suyash rather than schedule unilaterally.

Current model: **`qwen2.5:14b`** (general instruct). Chosen 2026-08-18 over `qwen2.5-coder:14b`,
which is code-tuned and weaker at policy/HR prose.

**Keep `qwen2.5-coder:14b` installed.** The shared Dify is configured against it. It is company
infrastructure used by several people including the tech head, not a teammate's scratch project, so
it is not ours to repoint — and deleting the model would break it. Since that second model is
therefore permanent, capping Ollama to one *resident* model
(`OLLAMA_MAX_LOADED_MODELS=1`) is the entire VRAM mitigation rather than half of it.

**Do not re-evaluate GPT-OSS 20B.** It was tested on 2026-08-07 and rejected: it returned empty
responses despite fitting in VRAM (`internship-tracking/resume_impact_tracker.md:212`). It was
deleted on 2026-08-18. `SERVER_SETUP.md:92-100` keeps the VRAM arithmetic explaining why a 14 GiB
model never really fit alongside the other tenant.

---

## Things that fail silently

### The pipeline code lives in a database, not this repo

`open_webui_pipeline/sparkline_pipeline.py` is a *copy*. The executing version is in Open WebUI's
SQLite `function` table under id `sparkline`. Editing the repo file changes nothing until that
column is updated and `sparkline_webui` is restarted.

### Adding a user takes three steps across two databases

Doing only the first is the easy mistake — the account looks created and the person still cannot
use anything.

1. **Sparkline API account** — `python -m db.seed_pilot_users --password '<pw>' --apply`
   (idempotent; deactivates rather than deletes users referenced by documents or the audit log).
2. **Open WebUI account** — separate DB (`/app/backend/data/webui.db` in `sparkline_webui`).
   Signup is disabled and the default role is `pending`, so create explicitly with `role="user"`
   via `Auths.insert_new_auth`. Both `get_password_hash` and `insert_new_auth` are **async** — a
   sync call silently writes a coroutine and leaves a half-created user. `WEBUI_SECRET_KEY` exists
   only in PID 1's environment: `export $(tr '\0' '\n' < /proc/1/environ | grep ^WEBUI_SECRET_KEY)`.
3. **Model access grant** — without it the user logs in and sees *no models at all*. Open WebUI's
   rule is "no DB entry means admins only". Create the model row, then
   `POST /api/v1/models/model/access/update` with a **per-user** grant. `principal_type: "anyone"`
   is silently stripped unless `sharing.public_models` is enabled.

The pipe matches users **by email**, not display name (`_identity()` in `sparkline_pipeline.py`),
so the Open WebUI email must exactly equal the API account's `email` column.

### Editing `.env` needs the container *recreated*, not restarted

`docker-compose.yml:155` uses `env_file: .env`, which bakes every value into the container's
environment at **creation** time. `docker restart sparkline_api` therefore keeps the old values and
the app silently runs on stale config — `printenv` inside the container still shows the previous
value while the file on disk shows the new one. Always:

    docker compose -f docker-compose.yml -f docker-compose.server.yml up -d api

Pass **both** compose files. `docker-compose.server.yml` is what supplies GPU access and
`host.docker.internal`; recreating with only the base file drops embedding to CPU (~1 hour for a
large workbook instead of ~1 minute) without erroring. Verify after any recreate:

    docker exec sparkline_api python -c "import torch; print(torch.cuda.is_available())"

### pytest is not in the image

`Dockerfile:18` runs `poetry install --no-dev`, so the test dependencies are absent from the image.
Whatever pytest you find in a running container was `pip install`ed by hand and **is destroyed by
any recreate**. After recreating, restore it before testing:

    docker exec sparkline_api pip install --no-cache-dir \
        "pytest>=8.2" "pytest-asyncio>=0.23" "pytest-httpx>=0.30"

The durable fix is a dev-dependency layer or a test stage in the Dockerfile; until then, treat "the
tests vanished" as expected rather than as breakage.

### New Ollama models appear in the tester dropdown

Open WebUI reads models straight off host Ollama (`docker-compose.server.yml:41`). Newly pulled
models default to visible, and a tester who picks a raw model gets an **ungrounded** chatbot with
no retrieval and no citations. Every raw model must be set `is_active = 0` in the `model` table;
only the `sparkline` pipe stays active. Re-check this after any `ollama pull`.

---

## Routing

Retrieval runs first; the best passage's rerank score picks the answer mode.

    score >= high         → document answer with citations
    low <= score < high   → blended: documents plus general knowledge, model flags which is which
    score <  low          → general knowledge only, no context, no citations

`ROUTER_MODE=legacy` restores the old always-RAG behaviour as a one-variable rollback.

**Open defect (2026-08-18).** `config/settings.py:162-163` defines the band as high `-2.0` /
low `-5.5`, but `.env:76-77` sets **both to `-6.0`**. Because `router/query_router.py:212`
computes `blended = score < high` only after `score >= low` has passed, an equal high and low makes
`blended` permanently `False` — the blended band is unreachable and no answer ever marks which
parts came from general knowledge. Precommit checks at `eval/precommit_checks.py:239` and `:242`
exist to catch exactly this. Resolve it during the next calibration, not by guessing.

**Thresholds are measured, never invented.** `eval/calibrate_router.py` produces them. Run against
the current corpus on 2026-08-18 it reported clean separation and recommended
`HIGH=-4.58` / `LOW=-5.47`:

    in-corpus  n=12  min=-3.670  median= 5.893  max= 9.088
    typo'd     n=12  min=-3.670  median= 5.893  max= 9.088
    general    n=15  min=-11.308 median=-9.760  max=-6.352
    gap = 2.682   TYPO TOLERANCE: PASS

Note the recommended `LOW` (-5.47) is **tighter** than the live value (-6.0), and the recommendation
restores `HIGH > LOW`, which would make the blended band reachable again. Applying it is a
behaviour change, so decide it deliberately. These numbers were measured against 186 Qdrant points
from only two documents and **are invalid the moment real tester documents are ingested** —
recalibrate as part of that step.

### `IN_CORPUS` questions are written in the documents' own vocabulary

The corpus is the project's *own* design documents, so `IN_CORPUS` in `eval/calibrate_router.py`
asks things like "What does the memory manager track instead of raw message history?" Real testers
will not phrase questions that way, so "clean separation" on this set is weaker evidence than it
looks. Treat the first week of real tester questions as the actual calibration signal.

**`project work split.docx` is misleadingly named** — it is an architecture guide (layers,
ownership, capabilities), not a team roster. Asking it "what is the work split between team
members?" scores about -8.3 and correctly routes to general, because that content is genuinely not
in the corpus. Do not read that as a routing bug; check what a document actually contains before
concluding the gate is wrong.

Typo tolerance (`retrieval/query_normalizer.py`) is part of routing, not cosmetic: a misspelled
document question retrieves worse, scores lower, and falls out of the document band. Corrections
are drawn from the vocabulary of whatever documents are currently ingested and rebuilt on every
ingestion, so it follows new uploads with no code change. The acceptance bar is the
`IN_CORPUS_TYPOD` group in `eval/calibrate_router.py` plus check 2b in `eval/precommit_checks.py`.

### The five "human read" checks are by design

`eval/precommit_checks.py` prints five `review()` items every run (lines 172, 224, 239, 242, 315).
They are **not** open TODOs and not failures — they are assertions that cannot be made
mechanically (does the blended answer visibly mark its sources, does the typo fix read correctly,
is the gate's latency cost acceptable). Someone reads them each run. Do not "fix" them by
converting them to asserts.

---

## Credentials

Never paste secret values into a transcript, a commit, or an issue. Real values live in
`.env` on the server (git-ignored); `.env.example` documents every key with placeholders.

| What | Where | Notes |
|---|---|---|
| `SERVICE_TOKEN` | `.env` | The pipe authenticates with this, **never** with user passwords. |
| `file.admin` login | `SPARKLINE_ADMIN_PASSWORD` env var, or the CLI prompts | Rotated 2026-08-18; the old `FileAdmin@2025` no longer works. There is **no default in the source** any more — `admin_tools/ingest_cli.py` reads the env var and prompts if it is unset. |
| Pilot user passwords | bcrypt hashes only | **Not recoverable.** Reset via the admin-reset flow; do not attempt to read them back. |
| `WEBUI_SECRET_KEY` | PID 1 env of `sparkline_webui` | Not in any file. |

Since the 2026-08-13 auth rework the two password sets are **independent**: Open WebUI passwords
are what testers log in with; the API no longer sees them at all. Be explicit about which set you
are handing out. Do not reintroduce a shared user password anywhere.

---

## Running it

There is **no Poetry and no virtualenv on the server host** — the repo is bind-mounted into
`/app` in `sparkline_api`, so everything runs inside that container against the live source. No
rebuild is needed after editing a `.py` file.

    docker compose -f docker-compose.yml -f docker-compose.server.yml up -d

    docker exec sparkline_api python -m pytest -q            # 75 unit tests, ~3s
    docker exec sparkline_api python -m eval.precommit_checks # 22 live checks + 5 human reads
    docker exec sparkline_api python -m eval.calibrate_router # re-measure routing thresholds

API on `:18000` (`/health`, `/docs`). Open WebUI is the tester-facing frontend.
Embedding runs on CUDA; the reranker is deliberately on CPU because VRAM is shared.

`poetry install` / `poetry run pytest` only apply to a laptop checkout, where Poetry is present.

---

## Document deletion policy

Deleted documents are destroyed immediately — no retention window, no grace period, no separate
permanent-destroy step. Decided 2026-08-20 (manager). Deletion already removes the underlying
file, not just the index entry.

## Pending work

1. **Release to the six testers.** Blocked on the corpus: `demo_docs/` still holds only the two
   development documents from 2026-08-07. Ingest the testers' real documents, then recalibrate
   (see above), then release.
2. **Per-chat file upload with session-scoped retrieval.** Blocked by access control, not parsing.
   `access_control/pep.py:52-53` returns `must=[is_active_version]` for `full_access=True` users —
   which every pilot user is — so a session-uploaded document would be visible to **every** user.
   Needs session scoping in the Qdrant filter, a TTL for temporary chunks, and the pipe to forward
   attachments (it currently reads only `body["messages"]`). Build and test this **before**
   testers are live, never during.
   **Contradiction RESOLVED 2026-08-21 — upload is OFF.** Checked in the live UI as a pilot user:
   the `+` menu renders "Upload Files" / "Attach Files" as **greyed-out placeholders**, not usable
   options. This file was right and the internship tracking docs were wrong. The pilot ships
   without per-chat upload, stated as a limitation in the tester manual. Enabling it on release day
   was rejected: it would put an isolation path that has never run in production in front of six
   people at once, which is what "build and test this **before** testers are live" was written to
   prevent.
3. **Build a standalone admin front end — deliberately separate from Open WebUI**, per manager
   direction (2026-08-19). It must talk only to the existing API (`POST /admin/ingest`,
   `DELETE /admin/documents/{id}`, `GET /admin/documents`, plus user/audit-log endpoints), never
   directly to the databases — a page hitting storage directly would recreate the network-exposure
   hole closed on 2026-08-19. Needs: upload/delete, per-document access-control editing (already
   built), and file download-back (not yet built). The admin currently uses raw `:18000/docs`.
4. **Prompt injection — tested and fixed 2026-08-19.** `eval/adversarial_checks.py` runs six
   areas against the live stack; it now reports **18 passed, 0 failed** (from 11/4 before the fix).
   What was wrong and what closed it:
   - **Document-borne injection** — a file whose contents carried instructions was obeyed, printing
     the system prompt. Fixed by fencing every retrieved passage in an explicit data region
     (`retrieval/prompt_defence.py`) plus an instruction hierarchy in both system prompts. The
     fence marker is stripped from passage text first, or a document containing it could close the
     region early and have the rest read as instructions.
   - **System prompt disclosure** — leaked to "repeat everything above this line". Fixed by a
     non-disclosure rule and `scrub_prompt_leak()` on the way out.
   - The disclosure hole was **only closed once the general agent was fixed too**. The guard went
     into the document agent first, but those probes route to general — the hole was open on the
     busier path. Worth remembering: there are two answer paths, and a defence on one is not a
     defence.
   The leak guard is deliberately narrow: "construction and equipment company" is in the prompt but
   is also an ordinary thing to say about Sparkline, and the standard refusal comes from the prompt
   and must stay recognisable to the router. A false positive replaces a correct answer with a
   refusal, which is worse than the leak.
   **Access control held throughout, before and after.** Another user with the correct chat ID never
   extracted an attachment, because isolation is enforced in the Qdrant filter rather than by the
   model's good behaviour.
   Re-run after any prompt change, along with `eval.precommit_checks` (21 passed, 0 failed after
   this change — prompt edits are exactly what regresses routing and refusal detection).
5. RAGAS baseline (`eval/ragas_runner.py`) — must run *after* the routing change; figures measured
   under the old always-search behaviour are not valid.
6. Real per-user access restrictions, gated on HR supplying department and designation data.
7. **File export delivery — DONE, verified end to end on the server 2026-08-21.** This entry
   previously said delivery was missing; that was true on 08-18 and was closed on 08-19. It is
   recorded here because the stale version already caused one wrong conclusion (that image
   delivery would have to wait for this layer to be built).
   What exists now: `gateway/routes/exports.py` persists the generated file to MinIO and serves it
   at `GET /exports/{export_id}?token=...`, with `gateway/middleware/download_token.py` issuing a
   token scoped to **one file and one person** — a browser can follow the link with no auth header.
   Verified 08-21 as a real pilot user: the reply carried a `download_url`, an unauthenticated
   fetch of it returned HTTP 200 and a valid 36 KB `.docx` (17 zip members).
   Still open around it: `tools/chart_tool.py` is **unverified** end to end, and the
   `tools/sandbox.py` subprocess path was never exercised. On 08-21 a chart request produced *no*
   tool call because the corpus holds no numeric data — the model asked for data instead, which is
   correct behaviour but reads to a tester as a refusal.
   Also unresolved from the 08-18 run: an export request with poor retrieval
   ("download the project work split as a spreadsheet", rerank -7.98) produced no tool call *and*
   answered in Thai. On a weak export hit the model both skips the tool and drifts language.
8. Enterprise ERP/HRMS routing — gated on Dhruv, deliberately not half-wired. The agreed contract
   and his answers are in `ENTERPRISE_ROUTING_CONTRACT.md`; `agents/enterprise_agent_interface.py`
   is an abstract contract with no implementation.

---

## Pilot day — state verified on the server, 2026-08-21 (morning)

Everything below was **run on `SEPL-PC`**, not inferred from this checkout. Re-verify rather than
trusting these numbers if more than a day has passed.

    ssh sparkline            # ~/.ssh/config: HostName 192.168.200.21, User sepl, key sparkline_server
    cd ~/proj1/sparkline-assistant

| Check | Result |
|---|---|
| Containers | all 7 `sparkline_*` up; `/health` returns ok |
| `python -m pytest -q` | **230 passed** (~8s) |
| `python -m eval.precommit_checks` | **21 passed, 1 failed, 5 human reads** |
| Accounts | 8 in Postgres *and* 8 in `webui.db`, emails match |
| Model access grants | 7 per-user rows for the `sparkline` pipe |
| Model gate | `qwen2.5:14b` and `qwen2.5-coder:14b` both `is_active=0`; only `sparkline` active |
| Executing pipe | byte-identical to `open_webui_pipeline/sparkline_pipeline.py` |
| Real pilot-user query | document answer w/ 5 citations; "tell me a joke" routed to general |
| Export to download | HTTP 200, valid 36 KB `.docx`, no auth header needed |
| Signup / web search | both disabled |

The **one failing check is the known routing band** (`HIGH == LOW == -6.0`, see Routing above). It
is meant to fail. Do not "fix" it by inventing numbers.

**A second failure appeared after ingestion (2026-08-21 afternoon): 20 passed, 2 failed.** Check 4's
mid-conversation turn `by the way what is 2 + 2` routed to `document_rag` at score **1.66** instead
of general. It is not the gate failing generally — standalone general questions still route
correctly, verified live in the UI (`tell me a joke`, `what is our leave policy`, `who is the CEO
of Google` all answered from general knowledge with no citations). What leaks is a **topic switch
mid-chat**: the query is contextualised against the previous document turn, so it retrieves as
though still on that subject and clears the -6.0 floor easily. The answer was still correct and
honestly labelled ("not directly provided in the source documents"), so this was accepted as a
manual line — *start a new chat when you change topic* — rather than a release blocker. It is also
the strongest argument for recalibrating: a general question scoring 1.66 means -6.0 has almost no
margin against contextualised queries.

### The corpus blocker is CLEARED — 8 real documents ingested 2026-08-21

The tester documents arrived as **8 crane and hoist product catalogues** (PDF) and were ingested
as `file.admin` via `admin_tools/ingest_cli.py`, all `--public` and untagged (HR still has not
supplied departments; label later with `set-access`). Qdrant `sparkline_documents` went
**186 → 2857 points**; BM25 corpus = 2764 chunks. The ~93 gap is chunks with no indexable tokens.

| Document | Pages | Chunks | chunks/page | needs_ocr |
|---|---|---|---|---|
| Wire rope hoists Product information_NEW.pdf | 216 | 899 | 4.2 | 3 |
| End Carriages_2020-02-1.pdf | 144 | 520 | 3.6 | 3 |
| Chain hoists Product information.pdf | 96 | 327 | 3.4 | 0 |
| DEMAG.pdf | 180 | 361 | 2.0 | 0 |
| eepos-Catalogue-2019.pdf | 156 | 294 | 1.9 | 4 |
| handtakels-yale_0.pdf | 44 | 80 | 1.8 | 0 |
| LR COMPONANT Manuals.pdf | 81 | 97 | 1.2 | 2 |
| FHBR-011223-Finrae-catalog-update-2024.pdf | 104 | 93 | **0.9** | 4 |

`needs_ocr` is 0–4 everywhere, so these are **native-text PDFs, not scans** — no Tesseract
guesswork to distrust. **File size does not predict text volume**: `LR COMPONANT` is 37.8 MB and
yielded 97 chunks, because the bulk of it is images sitting alongside the text.

`FHBR-011223-Finrae` is the weak one — 0.9 chunks/page and `avg_tokens=199` against ~320–360
elsewhere. Its pages are genuinely thin, so most of that catalogue's content is in graphics that
the parsers drop. Expect poor answers on Finrae products specifically.

**The image limitation, confirmed on real content.** Asked for a value off a graph, the assistant
returned the *formulas and method for reading the diagram* — the surrounding prose — because it
never saw the curve. Asked the same kind of question against a real **text** table (aluminium XL
profile weights), it answered correctly and the numbers verified by hand. That contrast is the
clearest available demonstration and belongs in the tester manual.

**Recalibration is still outstanding** and is now the top job. See Routing: `IN_CORPUS` /
`IN_CORPUS_TYPOD` in `eval/calibrate_router.py` still ask about the old design docs in their own
vocabulary, so the calibration currently measures almost nothing about the real corpus.

### GPU contention is the practical risk today, not correctness

Measured 2026-08-21 09:05 IST: card at **15404 / 16311 MiB**, three tenants —

    1788619   6762 MiB   /usr/bin/python3           # Suyash's CV stack (host, outside Ollama)
     717198   1506 MiB   /usr/local/bin/python3.11   # our embedding + reranker
     923266   6976 MiB   /usr/local/bin/ollama       # our LLM

`ollama ps` showed `qwen2.5:14b` at **31%/69% CPU/GPU** — it does *not* fit. Real end-to-end
timings as a pilot user, same moment:

    document question ("What is stored in MinIO?")   9.41 s   (tracking docs benchmark: ~1.4 s)
    general question  ("Tell me a joke")             2.68 s   (benchmark: ~1.7 s)

Six testers on that will report "it's broken" when it is contention. Coordinating with Suyash buys
more on pilot day than any code change. Quote the benchmark numbers only alongside the contention,
or they misrepresent what a tester will actually see.

### Images in source documents are silently dropped

Established 2026-08-21 by reading the parsers. An image embedded in a document is discarded at
parse time in **every** supported format, with no error and no reference on the stored chunk:

- `docx_parser.py` walks paragraphs, tables and textboxes; never `w:drawing` / `w:pict`.
- `excel_parser.py` records a chart's **title** only (`_get_chart_title`), not the chart.
- `pdf_parser.py` takes text only; a scanned page is OCR'd to text and the pixels are dropped.
- The Qdrant chunk payload has **no field** capable of holding an image reference at all.

So this is a data-model gap, not an unwired connection. A tester whose answer lives in a diagram
gets the surrounding prose or nothing, and is never told anything was omitted. The pilot ships with
this as a stated limitation. The two ways of closing it, and why each is deferred, are in
`internship-tracking/FUTURE_SCOPE.md`.

---

## `internship-tracking/FUTURE_SCOPE.md` is the register of deferred work

Created 2026-08-20. Four items, each with the reason it is not being built yet: image extraction
and delivery, full multimodal (vision-model) retrieval, the standalone admin UI, and the pilot's
manual system-switch dropdown. **Read it before re-planning any of those** — the reasons are the
point, and re-deriving them from the code has already gone wrong once.

### Admin UI — design settled 2026-08-20, deliberately not built

Held on coordination with Dhruv, not on any technical question. Settled scope:

- **Documents only** this pass — user admin and the audit log stay on the CLI / `:18000/docs`.
- Login (`POST /auth/login`, JWT — already built, already used by `admin_tools/ingest_cli.py`),
  list (`GET /admin/documents`), upload (`POST /admin/ingest`), access edit
  (`PATCH /admin/documents/{id}`), delete (`DELETE /admin/documents/{id}`) — **all already exist**.
- The **only** backend gap: an authenticated route to download an original file.
  `services/minio_service.py:102 download_document()` exists and is called by nothing; copy the
  token pattern from `gateway/routes/exports.py`.
- Ingest is **synchronous** — parse+chunk+embed+index all in-request, no job-status endpoint.
  Accepted at pilot scale: spinner and wait. Do not build a job queue for this.
- A client of the API only, never touching storage directly — that constraint is what keeps the
  2026-08-19 network exposure closed.
- **Build it as a plain HTML + vanilla JS page served by FastAPI `StaticFiles`.** There is no
  `node` or `npm` on the laptop (see below), the scope is six actions on one screen, and this
  deploys by virtue of living in the repo the API already serves. Estimated ~1 focused day.
  `gateway/main.py:45` already sets `allow_origins=["*"]` (its own comment says to restrict that in
  production), so CORS will not bite during development.

---

## Facts the code does not tell you — environment

### Server

- Postgres is `POSTGRES_DB=sparkline_db`, user `sparkline`, host port **15432** (not a `sparkline`
  database, not 5432). Use
  `docker exec sparkline_postgres psql -U sparkline -d sparkline_db`.
- `services/qdrant_service.py` exposes **module-level functions** (`_get_client()`, `upsert_chunks`,
  `search_dense`, ...). There is no `get_qdrant_service()` — importing one fails.
- Qdrant collections: `sparkline_documents`, `sparkline_session_docs`.

### `webui.db` schema differs from what the three-step user procedure above describes

That procedure is still right in intent, but this Open WebUI version stores grants differently, so
verify against the real shape rather than the described API:

- Model grants live in their own table: `access_grant(id, resource_type, resource_id,
  principal_type, principal_id, permission, created_at)` — **not** an `access_control` column on
  `model`. A per-user grant is `('model', 'sparkline', 'user', <user_id>, 'read')`.
- `config` is **key/value** (`key`, `value`, `updated_at`), not one JSON `data` blob. Keys are
  dotted, e.g. `ui.enable_signup`, `web.search.enable`, `rag.*`.
- `model` columns: `id, user_id, base_model_id, name, params, meta, updated_at, created_at,
  is_active`.

### Laptop

- **No `node`, no `npm`, no `gh`.** Only `git`. This is why the admin UI is planned as a no-build
  static page, and why GitHub work goes through plain `git` over HTTPS (Git Credential Manager holds
  the token) rather than the `gh` CLI.
- A Poetry venv **does** exist at `.venv/` and has `python-docx`. Use `.venv/Scripts/python.exe` to
  read or edit the `internship-tracking/*.docx` trackers. **Do not run `poetry install`** — it
  destroys the torch runtime shim.
- When editing those `.docx` files, clone an existing `List Paragraph` element and swap its text.
  Setting the style name alone loses the bullet, because numbering lives in the paragraph's `pPr`,
  not in the style. Set `PYTHONIOENCODING=utf-8`, or printing em-dashes to the console raises.

### Repos — "my repo" vs "our repo"

- **"my repo"** = `github.com/sidharth-vijayan/sparkline-assistant`. This checkout's `origin`.
- **"our repo"** = `github.com/sidharth-vijayan/sparkline`, shared with Dhruv. **Not** a configured
  remote here. Branches as of 2026-08-21: `main`, `dhruv/erp-adapter`, `dhruv/erp-wiring` — follow
  that convention with `sidharth/<topic>`.
- Write access to the shared repo is confirmed (via `git push --dry-run`). **Ask before pushing
  there anyway** — Dhruv watches it, and a surprise branch is a conversation.
- Its `main` **shares no history with this checkout** (`22dfaa40...` is not a local object). Settle
  whether it holds a copy of this codebase or only the integration layer *before* pushing anything.

---

## Next task

The **tester instruction manual**, written for people using a system like this for the first time:
how to open the UI, send a message, upload a file if that turns out to be enabled (see the
contradiction on pending item 2), and — as importantly — what the pilot cannot do yet, so an absent
capability is not reported as a fault. Known limits to state plainly: images in documents, charts
without numeric data, ERP/HRMS questions, and today's response times under GPU contention.

---

## Do not

- Touch `docker-*` containers or `/home/sepl/dify` (shared company Dify), or `~/cv` and
  `~/.ssh/isv_deploy` (Suyash's CV project). Same `sepl` user, different owners.
- Repoint or delete `qwen2.5-coder:14b` — the shared Dify depends on it.
- Commit the `.bak` rollback files (`.env.bak-*`, `docker-compose.server.yml.bak-*`). They are
  intentionally untracked.
- Edit the Pilot 1 column of `PILOT_VS_PERFORMANCE.md` (renamed from `PILOT_1_BASELINE.md` on
  2026-08-21, since it spans every pilot rather than only the first). It is a frozen before/after
  measurement. Later columns are meant to be filled; that one is not.
- Commit from the server now that development has moved to the laptop — concurrent commits from
  both ends produce drift that will not fast-forward.
