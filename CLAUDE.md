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

## Pending work

1. **Release to the six testers.** Blocked on the corpus: `demo_docs/` still holds only the two
   development documents from 2026-08-07. Ingest the testers' real documents, then recalibrate
   (see above), then release.
2. **Per-chat file upload with session-scoped retrieval.** Blocked by access control, not parsing.
   `access_control/pep.py:52-53` returns `must=[is_active_version]` for `full_access=True` users —
   which every pilot user is — so a session-uploaded document would be visible to **every** user.
   Needs session scoping in the Qdrant filter, a TTL for temporary chunks, and the pipe to forward
   attachments (it currently reads only `body["messages"]`). Per-chat upload is switched off in
   Open WebUI meanwhile. Build and test this **before** testers are live, never during.
3. **Move upload/withdrawal into Open WebUI.** Endpoints exist: `POST /admin/ingest`,
   `DELETE /admin/documents/{id}`, `GET /admin/documents`. The admin currently uses `:18000/docs`.
4. Prompt-injection and adversarial security testing against the live pipeline.
5. RAGAS baseline (`eval/ragas_runner.py`) — must run *after* the routing change; figures measured
   under the old always-search behaviour are not valid.
6. Real per-user access restrictions, gated on HR supplying department and designation data.
7. Tool-calling repair for chart and export generation (`tools/`).
8. Enterprise ERP/HRMS routing — gated on Dhruv, deliberately not half-wired. The agreed contract
   and his answers are in `ENTERPRISE_ROUTING_CONTRACT.md`; `agents/enterprise_agent_interface.py`
   is an abstract contract with no implementation.

---

## Do not

- Touch `docker-*` containers or `/home/sepl/dify` (shared company Dify), or `~/cv` and
  `~/.ssh/isv_deploy` (Suyash's CV project). Same `sepl` user, different owners.
- Repoint or delete `qwen2.5-coder:14b` — the shared Dify depends on it.
- Commit the `.bak` rollback files (`.env.bak-*`, `docker-compose.server.yml.bak-*`). They are
  intentionally untracked.
- Edit the Pilot 1 column of `PILOT_1_BASELINE.md`. It is a frozen before/after measurement.
- Commit from the server now that development has moved to the laptop — concurrent commits from
  both ends produce drift that will not fast-forward.
