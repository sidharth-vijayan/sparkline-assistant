# Commit plan — evidence-gate routing work

**Status: CLOSED — all 16 commits landed and pushed as of Thu 2026-08-13.** The final 6 (group 3,
listed below) were committed and pushed that morning; `git log --oneline origin/main..HEAD` is empty.
Nothing further to commit from this plan. The only remaining Thursday item is the typo-tolerance
build (see below), whose commit messages still need writing once that code exists.

The routing work is finished, tested and running on the server. It was being committed a few files a
day, one commit per file. The schedule moved a day earlier than originally planned: Wednesday's and
Thursday's groups both landed on Wed 2026-08-12, so the remaining group moves to Thursday.

**If you are a fresh session picking this up: read this file, then run `git log --oneline -12` in
`~/proj1/sparkline-assistant` to confirm what already landed. Commit the group below, then push —
the contribution graph does not update until you push.** The git log is the source of truth, not
the date: if a day was skipped, carry on from where the log ends.

- Repo: `/home/sepl/proj1/sparkline-assistant`, branch `main` (its whole history is on main).
- Identity is already configured repo-locally: `Sidharth Vijayan <sidharthclt12@gmail.com>`.
- Every commit body ends with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Push works: remote is `git@github-sidharth:sidharth-vijayan/sparkline-assistant.git`, using
  `~/.ssh/sparkline_gh2` via the `github-sidharth` alias in `~/.ssh/config`. Verified end to end.
- Backup of all pending work: `/home/sepl/proj1/.routing-work-backup/` (patch + new files). The
  working tree is the source of truth; regenerate from it rather than applying the patch.
- This file lives outside the repo and is never committed.

## Thursday also carried the typo-tolerance build — done

**Updated 2026-08-13.** Typo tolerance was built, verified and is live. `HANDOFF_2026-08-11.md` has
since been deleted at the user's request; the design rationale now lives in the docstrings of
`retrieval/query_normalizer.py`, and the acceptance bar is enforced by the `IN_CORPUS_TYPOD` group in
`eval/calibrate_router.py` and check 2b in `eval/precommit_checks.py`.

A further 8 commits landed on Thursday covering the account and authentication rework. The remaining
working-tree changes (typo tolerance, document formats, GPU embedding, the enterprise contract) are
still uncommitted and were deliberately left for Friday.

This plan closes once both are done. Friday is then the pilot itself plus whatever Dhruv's ERP/HRMS
answer makes possible.

---

# Thu 2026-08-13 — surfaces, tests, tooling (6 commits)

No ordering constraints remain within this group. The two that applied
(`ingestion/bm25_index.py` before `eval/calibrate_router.py`, and `router/route_decision.py` before
`tests/test_unit_components.py`) were both satisfied by the commits that already landed. Committing
in the order below is fine.

**11. `gateway/routes/chat.py`**
```
feat(api): expose the rerank score behind the routing decision

Lets the score bands be retuned against real pilot traffic instead of only the
calibration set.
```

**12. `open_webui_pipeline/sparkline_pipeline.py`**
```
fix(webui): keep Open WebUI task requests out of the RAG pipeline

Open WebUI issues title and tag generation requests against the selected model.
Each was costing a full PDP, retrieval, rerank and GPU pass, landing in the
Redis history as a 2KB user prompt, and surfacing in the chat — a "hi" once came
back as {"title": "Greeting"}. They are now answered locally.

Also label whether an answer came from Sparkline documents or general knowledge:
routing is automatic, so the user never chose a mode and cannot otherwise tell
a grounded answer from an ungrounded one.
```

**13. `db/models.py`**
```
docs(db): document the agent_type values the router writes

The routing change adds document_rag_blended and general_fallback alongside the
existing values; the column comment listed only the original three.
```

**14. `tests/test_unit_components.py`**
```
test(router): cover pre-routing, refusal detection and follow-ups

Includes the exact queries that reached users incorrectly, as regression guards.
Also pins qdrant_host in the settings test, which inherited the ambient
environment and could never pass inside the api container.
```

**15. `eval/calibrate_router.py`**
```
feat(eval): add a harness to measure routing score thresholds

Cross-encoder scores are raw logits, not probabilities, and the useful split
point depends on the corpus, so the thresholds have to be measured rather than
guessed. Prints in-corpus against out-of-corpus scores and reports the overlap.

The in-corpus questions must be rewritten whenever the document set changes;
eval/golden_set.json is not usable for this, as it asks about documents that
have never been ingested.
```

**16. `eval/precommit_checks.py`**
```
test(eval): add live regression checks for the routing change

Covers what the routing matrix does not: session-history integrity, the
general-knowledge fallback, blended-mode honesty, mixed conversations,
degenerate input, gate latency and audit-log wiring.
```

---

## Verification status — all six files were exercised on 2026-08-12

Run inside the `sparkline_api` container against the live stack:

| suite | command | result |
|---|---|---|
| unit tests | `python -m pytest tests/ -q` | **45 passed** in 0.53s |
| live regression | `python -m eval.precommit_checks` | **16 passed, 0 failed**, 4 flagged for human read |
| calibration | `python -m eval.calibrate_router` | clean separation, gap 2.682 |

**Expect 16 passed / 4 read, not 17 / 3.** Check 3 ("Blended mode flags general knowledge") moved
from PASS to READ once blending was switched off later the same day — see the section below. It
reports `expected document_rag_blended, got document_rag`, which is now the *intended* result. The
check is not wrong; it predates the decision. If it stays noisy, retire that check rather than
re-enabling blending.

The "human read" items are not failures — they are judgement calls the script cannot make:
general-knowledge fallback never triggered (the gate routed directly, which is acceptable), the
blended-band check described above, blended answers needing a human to confirm labelling (now moot),
and the latency table needing a human to accept the gate's cost on general questions (0.68s).

`pytest` is pip-installed into the running container and is **not** in the image
(`poetry install --no-dev`). It will vanish on rebuild — reinstall before relying on it.

## Blended mode was switched OFF on 2026-08-12 — decided and applied

Sidharth's decision: document answers must come from the documents only, never mixed with general
knowledge. Blended mode is therefore disabled. `.env` now reads:

```
ROUTER_RAG_SCORE_HIGH=-5.0
ROUTER_RAG_SCORE_LOW=-5.0
```

**How this works — no code change was needed.** `router/query_router.py:206` applies the floor
(`score < LOW` → general), then line 212 computes `blended = score < HIGH`. With `HIGH == LOW` that
condition is unreachable, so routing is a clean two-way split: documents or general, never blended.

`-5.0` was chosen as the centre of the measured gap — in-corpus scores bottom out at `-3.670`,
general questions top out at `-6.352`, so a single cutoff at `-5.0` leaves ~1.34 clearance either
side. This also fixed the original problem in passing: under the old `HIGH=-2.0`, genuine document
questions scoring -3.67 → -2.0 were served blended answers; they now get fully grounded ones.

Verified after the change: API restarted, thresholds confirmed live via `get_settings()`, 45 unit
tests pass, 16/0 live regression, mixed-conversation routing still correct, degenerate input safe.

Backup of the previous config: `sparkline-assistant/.env.bak-2026-08-12` (`HIGH=-2.0`, `LOW=-5.5`).

**To restore blending** (should the pilot show too many questions falling through to general), set
`HIGH` back above `LOW` — e.g. the calibration's own suggestion, `HIGH=-4.58 LOW=-5.47` — and restart
`sparkline_api`. Re-run calibration after any typo-tolerance work, which will shift all scores.
