# Deploying on the shared GPU server

Notes from bringing this up on `SEPL-PC` (Ubuntu 24.04, RTX 5060 Ti 16 GB).
Read the gotchas before running anything — several are non-obvious and fail
silently rather than loudly.

## Workspace: stay inside the project directory

The machine is shared with another developer, and `$HOME` is common ground. Keep
everything — logs, scratch files, downloaded documents — inside:

```
~/proj1/sparkline-assistant
```

Do not write to `~` directly. Logs belong in `logs/` inside the project (that
path is gitignored). Two stray files were left in the home root during the
initial bring-up, `~/model_pull.log` and `~/build.log`; delete or move them.

## The server is shared

Other people's stacks run on this box and have been up for days. **Do not**
`docker compose down` from the wrong directory, and do not stop containers you
did not start. Tenants observed publishing on the host:

| Port | Owner |
|------|-------|
| 80, 443 | `docker-nginx-1` |
| 8000 | `isv-app-1` |
| 6379 | `docker-redis-1` |
| 3001 | `isv-dashboard-1` |
| 5003 | `docker-plugin_daemon-1` |
| 11434 | native Ollama (we use this deliberately — see below) |

Our stack therefore publishes on a `1xxxx` block. Re-check before assuming
these are still free:

```bash
for p in 15432 16379 19000 19001 16333 16334 18000 13000 21434; do
  ss -tln | grep -qE "[:.]${p}\b" && echo "$p BUSY" || echo "$p free"
done
```

## Gotcha 1: the port variables do double duty

`docker-compose.yml` uses `${POSTGRES_PORT}` etc. for the **host** port
mapping, but `config/settings.py` reads those *same* variables to build the
connection URLs the app dials. Setting `POSTGRES_PORT=15432` to dodge a port
clash also makes the app try to reach `postgres:15432`, which nothing listens
on — the container listens on 5432.

`docker-compose.server.yml` fixes this by pinning the in-network ports back to
the real container ports. **The mismatch between `.env` and that override is
intentional. Do not "correct" it.**

## Gotcha 2: use the native Ollama, not the containerised one

Ollama 0.18.2 is already installed on the host, already listening on 11434 on
all interfaces, and already has direct GPU access. It runs as the system user
`ollama`, so its model store is `/usr/share/ollama/.ollama/models` — **not**
`~/.ollama`, which does not exist for `sepl`. The containerised `ollama` service
uses a separate Docker volume, so running it would mean re-downloading 15 GB for
nothing.

That store already holds `qwen2.5-coder:14b` (9 GB) plus roughly 7 GB of a
partially downloaded `GPT-OSS-20B` blob from an interrupted pull. Re-running
`ollama pull` resumes from that partial data rather than starting over.

The override points `api` and `open-webui` at the host via
`host.docker.internal` + `host-gateway`. `OLLAMA_PORT=21434` in `.env` only
parks the unused containerised `ollama` out of the way — `open-webui` has a
`depends_on: ollama` so Compose starts it regardless; it just sits idle.

Do not use `docker compose --profile gpu up`. The base file defines both
`ollama` (no profile, so it always starts) and `ollama_gpu` (profile `gpu`) and
**both bind the same host port**, so the second fails to bind. That is what
`docker-compose.gpu.yml` exists to work around, and it is not needed here at
all because we use the native Ollama.

## Gotcha 3: never rename the project folder

Compose derives the project name from the folder basename, and the project name
determines volume names (`sparkline-assistant_postgres_data`, …). Renaming the
folder makes Compose create **fresh empty volumes** — the seeded users and every
ingested document vanish, and it presents as "the app randomly broke". Nesting
the folder inside parent directories is fine; renaming it is not.

## Gotcha 4: VRAM is the binding constraint

The GPU is shared. Measured while another tenant's Python process held 3.0 GiB:

| | |
|---|---|
| GPU total | 15.9 GiB |
| Other tenant | 3.0 GiB |
| Free | 12.9 GiB |
| `GPT-OSS-20B-i1-GGUF:Q4_K_M` | ~14.0 GiB |

It does not fit, and Ollama will quietly offload the remainder to CPU rather
than error — generation drops to a few tokens/sec. Note it barely fits even on
an idle GPU (14.0 of 15.9 GiB leaves under 2 GiB for KV cache).

Verify what actually happened rather than trusting the logs:

```bash
ollama ps                 # shows PROCESSOR as GPU / CPU / a split
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

If it is CPU-bound, options in order of preference: get the other tenant to
free their 3 GiB; pull a smaller quant (`i1-Q3_K_M`, ~10–11 GB); or fall back to
`qwen2.5-coder:14b`, which was already present on the box (9 GB, no download).
Keep embeddings and the reranker on CPU (`EMBEDDING_DEVICE=cpu`,
`RERANKER_DEVICE=cpu`) — putting them on the GPU competes with the LLM for the
scarce resource.

## `.env` for this server

`.env` is gitignored, so create it by hand. Generate a real secret — the app
rejects anything under 32 chars.

```bash
cd <project>
SECRET=$(openssl rand -hex 24)
cat > .env <<'EOF'
APP_ENV=development
APP_SECRET_KEY=__SECRET__
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

POSTGRES_USER=sparkline
POSTGRES_PASSWORD=sparkline_secret
POSTGRES_DB=sparkline_db
POSTGRES_HOST=localhost
POSTGRES_PORT=15432

REDIS_HOST=localhost
REDIS_PORT=16379
REDIS_PASSWORD=redis_secret
REDIS_DB=0
REDIS_SESSION_TTL_SECONDS=3600

MINIO_ENDPOINT=localhost:19000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minio_secret
MINIO_BUCKET_DOCUMENTS=sparkline-documents
MINIO_SECURE=false
MINIO_PORT=19000
MINIO_CONSOLE_PORT=19001

QDRANT_HOST=localhost
QDRANT_PORT=16333
QDRANT_GRPC_PORT=16334
QDRANT_COLLECTION_NAME=sparkline_documents
QDRANT_VECTOR_SIZE=1024
QDRANT_API_KEY=

LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL_NAME=qwen2.5:14b
LLM_MAX_TOKENS=4096
LLM_TEMPERATURE=0.1
LLM_TIMEOUT_SECONDS=120
OLLAMA_PORT=21434

EMBEDDING_MODEL_NAME=BAAI/bge-large-en
EMBEDDING_DEVICE=cpu
RERANKER_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L-6-v2
RERANKER_DEVICE=cpu

RETRIEVAL_TOP_K_DENSE=20
RETRIEVAL_TOP_K_BM25=20
RETRIEVAL_TOP_K_RERANK=5
RETRIEVAL_RRF_K=60

CHUNK_SIZE_TOKENS=400
CHUNK_OVERLAP_TOKENS=80

DEFAULT_PILOT_ROLE=pilot_user

API_HOST=0.0.0.0
API_PORT=18000
WEBUI_PORT=13000
EOF
sed -i "s/__SECRET__/$SECRET/" .env
```

## Bring it up

Every command needs **both** `-f` flags. Omitting the override silently gives
you an api container that cannot reach Postgres, Redis, Qdrant or the LLM.

```bash
C="docker compose -f docker-compose.yml -f docker-compose.server.yml"

$C config --quiet          # validate before building
$C build api               # ~15–25 min: apt + poetry, torch is large
$C up -d
$C logs -f api             # want: startup.minio.ok / startup.qdrant.ok / ready
```

Then seed the database. `init_db` creates the tables and the `file.admin`
account, and refuses to run without `ADMIN_INITIAL_PASSWORD` so that no known
password is ever shipped. The pilot roster is separate and idempotent:

```bash
$C exec -e ADMIN_INITIAL_PASSWORD='<choose one>' api python -m db.init_db
$C exec api python -m db.seed_pilot_users --password '<choose one>' --apply
```

Check it answers:

```bash
curl -s http://localhost:18000/openapi.json | head -c 200
```

- API docs → `http://<server-ip>:18000/docs`
- Open WebUI → `http://<server-ip>:13000`

## Shipping code changes to the server

Development happens on a separate machine; the server only ever pulls. There is
no file copying, and nothing is edited directly on the server.

```
edit + commit + push (dev machine)  ->  git pull (server)  ->  maybe rebuild
```

**Most changes need no rebuild.** The `api` service bind-mounts the repo
(`.:/app`) and runs uvicorn with `--reload`, so the container executes the
working tree directly. A `git pull` is picked up in seconds:

```bash
cd ~/proj1/sparkline-assistant
git pull origin main
$C logs api --tail 20        # WatchFiles logs the reload
```

If the reloader misses it (renamed or deleted modules sometimes confuse it):

```bash
$C restart api
```

**Rebuild only when the image itself changed** — that means `pyproject.toml`,
`poetry.lock`, or the `Dockerfile`. Adding a dependency is the usual trigger:

```bash
git pull origin main
$C build api && $C up -d api
```

**Changing `.env` always needs a recreate**, not a restart — env vars are fixed
at container creation:

```bash
$C up -d api
```

Note that `.env` is gitignored and therefore never arrives via `git pull`. If a
new setting is added on the dev machine, it must be added to the server's `.env`
by hand or the app falls back to the default in `config/settings.py` — which is
usually `localhost` and will fail confusingly inside a container.

Local commits on the server will make later pulls conflict. If a fix was made
directly on the box, push it from there or discard it:

```bash
git status                   # expect: clean, except .env and demo_docs/
```

## Ingest documents

Without documents the RAG side has nothing to retrieve and every answer is
empty. `demo_docs/` is gitignored precisely so real company files never get
committed — put them there, not anywhere else in the repo.

```bash
mkdir -p demo_docs
# copy documents into demo_docs/
$C exec api python -m admin_tools.ingest_cli upload demo_docs/FILE.pdf --public
```

`--public` skips department/designation filtering, which is what you want for a
demo — otherwise the document is invisible to whichever user you log in as and
it looks like retrieval is broken.

## Model download

15 GB at roughly 6 MB/s, so budget ~45 min. Run it detached so it survives a
dropped session, and note the progress bar writes `\r`:

```bash
nohup ollama pull hf.co/mradermacher/GPT-OSS-20B-i1-GGUF:Q4_K_M > logs/model_pull.log 2>&1 &
tail -c 300 logs/model_pull.log | tr '\r' '\n' | tail -3
```

Third-party HuggingFace GGUF pulls can fail on tag or filename mismatches, so
watch the first minute of that log rather than assuming it started cleanly.
