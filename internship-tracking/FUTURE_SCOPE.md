# Future Scope

Planned work that is deliberately **not** being built yet, and why. Each item names the
limitation that's holding it back (hardware, time, a dependency on other work, or a
person/coordination blocker) so it's clear later whether the blocker has actually cleared before
picking it up. Not a backlog of "nice to haves" — everything here was already discussed and
scoped, just sequenced after something else.

---

## 1. Image extraction + storage (images option 2)

Extract embedded pictures from source documents at ingest time (docx `w:drawing`/`w:pict`, xlsx
charts, pdf images — none of which are captured today, see "Images in source documents" below),
store them in MinIO, add an `image_refs` field to the Qdrant chunk payload, and return the
relevant image as a download link when its neighbouring text chunk is retrieved.

This is **retrieval-blind** — the system never looks at what's *in* the image, it just serves the
picture that sat next to the retrieved text.

**Why deferred:** Time/priority limitation. Not blocked on infrastructure: the delivery half is
already solved and can be copied rather than built. File export delivery landed 2026-08-19
(`gateway/routes/exports.py` — MinIO persistence plus `GET /exports/{export_id}` with a
single-file download token), so the storage-and-authenticated-link pattern this needs already
exists and works. What remains is genuinely new: pulling pictures out at parse time in each
parser (`docx_parser.py` never walks `w:drawing`/`w:pict`; `excel_parser.py` captures chart
titles only), and adding an image field to the chunk payload, which has none today. Deferred
because the pilot ships first, not because anything is in the way.

> **Note:** CLAUDE.md still lists file-export delivery as an open defect (pending work item 7).
> That entry is stale — it was closed on 2026-08-19. Worth correcting there so this reasoning
> isn't re-derived wrongly later.

---

## 2. Full multimodal retrieval (images option 3)

The model actually *sees* and reasons about image content — via a vision-capable local model —
so it can answer questions the image itself answers ("what does the equipment layout diagram
show?") and pick the right image by meaning, not just text proximity.

**Why deferred:** Hardware + scoping limitation. `qwen2.5:14b` (current model) is text-only; this
needs a vision-capable model (e.g. `qwen2.5vl`) on a GPU already contended by three tenants (see
CLAUDE.md — embedding/reranker, the shared company Dify's `qwen2.5-coder:14b`, and Suyash's CV
stack running directly on the GPU outside Ollama). Also needs its own retrieval-design work
(image-aware embeddings/chunking), which is a separate scoping exercise, not an extension of
option 1's plumbing. Not committed to; revisit only after a VRAM/model decision is made
deliberately, not squeezed in.

---

## 3. Standalone admin UI

A standalone frontend — deliberately separate from Open WebUI — for document management, per
manager direction (2026-08-19, see CLAUDE.md pending item 3). Talks only to the existing API,
never directly to the databases.

**Scope agreed (2026-08-20 planning):**
- Static React + Vite SPA, JWT bearer auth against the existing `POST /auth/login` (already
  built — no new auth work needed).
- Documents-only screens for this pass: list (`GET /admin/documents`), upload
  (`POST /admin/ingest`), edit access-control (`PATCH /admin/documents/{id}`, already built),
  delete (`DELETE /admin/documents/{id}`, already built), download-back (new — needs a
  `GET /admin/documents/{id}/download` route wrapping the already-existing but currently unused
  `minio_service.download_document()`).
- User management and audit-log screens are out of scope for this pass — endpoints already exist
  server-side, stay on raw `:18000/docs`/CLI for now.
- Ingest is a long synchronous HTTP call today (parse+chunk+embed+index in-request, no
  progress/job-status endpoint) — accepted for pilot scale: UI shows a spinner/disabled state and
  waits, no async job queue added.
- Verification is manual for now: upload real test files of each type via the new UI once built,
  confirm `chunks_created > 0` and a live query in Open WebUI retrieves them. No automated
  ingestion smoke test yet.
- Deployment: build to static files, run locally against the server first to verify end-to-end,
  containerize alongside the existing `docker-compose.server.yml` stack once proven.

**Why deferred:** Coordination limitation, not a technical one. This needs to be discussed and
synced with Dhruv before building, and that discussion/sync itself takes time given laptop-based
development on both sides. The design above is considered settled; only the "when do we start
building" decision is outstanding.

---

## 4. Manual system-switch dropdown in Open WebUI (pilot only)

For this pilot version, add a dropdown in Open WebUI letting testers manually pick which backend
they're talking to — the generic LLM/RAG system (this project) or Dhruv's ERP/HRMS system —
instead of one assistant routing automatically. Later, once both systems are live, this gets
replaced by semantic routing that infers which system a request belongs to from the request
itself, with no manual switch.

**Why deferred:** Dependency limitation. Dhruv's side isn't implemented yet — the enterprise
agent interface (`agents/enterprise_agent_interface.py`, CLAUDE.md pending item 8) is still an
abstract contract with no implementation, gated on him. The dropdown itself is a small Open WebUI
config/UI change; it can't be built before there's a second real backend for it to switch to.

---

## Related, already-known gaps

- **File export delivery** — **already done** (2026-08-19). `gateway/routes/exports.py` stores
  generated files in MinIO and serves them via `GET /exports/{export_id}` with a token scoped to
  one file and one person. Item 1 above copies this pattern rather than waiting on it. CLAUDE.md's
  pending-item list is stale on this point.
- **Chart generation** — still unverified end to end (`tools/chart_tool.py`, and the
  `tools/sandbox.py` subprocess path was never exercised). Shares the image-delivery question with
  item 1, so worth checking at the same time.
- **Per-chat session-scoped upload** — separate access-control problem, unrelated to
  admin-uploaded documents; not a prerequisite for anything above.
- **Admin UI download-back route** — the one backend piece item 3 needs.
  `services/minio_service.download_document()` already exists and is currently called by nothing;
  it needs a route, and `exports.py` shows the pattern for serving a file safely.
