"""
eval/accuracy_suite.py
───────────────────────
Accuracy measurements that need no human-written answers.

Confirming that an answer is *correct* requires someone who knows the documents,
and that person is the bottleneck on the whole accuracy question. Everything in
this file is what can be measured while waiting for them:

  1. Routing accuracy   — did a document question reach the document agent, and
                          a general question the general agent? Labels are
                          "in corpus" / "not in corpus", which anyone can supply
                          without knowing any answers.
  2. Abstention rate    — how often a question the corpus *does* cover comes back
                          as "I couldn't find this in the available documents".
                          Every one of these is a retrievable answer the user
                          never got.
  3. Recall@k           — did the citations include the document the answer
                          should have come from? Needs one label per question:
                          the filename. Minutes of a subject expert's time
                          instead of hours.
  4. Self-consistency   — ask the same question repeatedly and measure how far
                          the answers drift. Needs no labels at all.

What none of these establish is correctness. A system can route perfectly, cite
the right file, never abstain, answer identically every time, and still be
wrong. Read the caveat printed at the end of the run before quoting any of it.

Companion to eval/ragas_runner.py, which covers faithfulness and answer
relevance (also reference-free, but judged by an LLM rather than computed).

Run inside the api container:
    docker exec sparkline_api python -m eval.accuracy_suite
    docker exec sparkline_api python -m eval.accuracy_suite --input eval/accuracy_set.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import uuid
from itertools import combinations
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.table import Table

from config.settings import get_settings
from router.route_decision import is_refusal

# argparse rather than Typer: Typer 0.12 with click 8.2+ raises
# `Parameter.make_metavar() missing 1 required positional argument` before it
# parses anything, which silently broke every Typer CLI in this repo. See the
# longer note in admin_tools/ingest_cli.py.
console = Console()

BASE = os.getenv("SPARKLINE_API_URL", "http://localhost:8000")
USER = os.getenv("SPARKLINE_CHECK_USER", "sidharth.vijayan")


# ── Question set ─────────────────────────────────────────────────────────────
#
# A record is:
#   {"id": str, "question": str, "expects": "documents" | "general",
#    "source_document": str | None}
#
# `source_document` is the only field that needs someone who has read the
# corpus, and only recall@k uses it. Everything else runs on the in/out-of-corpus
# label alone.

def _default_question_set() -> list[dict]:
    """
    Fall back to the calibration sets, which are real and already labelled.

    This makes the suite runnable today rather than after an eval file is
    authored. It inherits a known weakness, restated at the end of the run:
    IN_CORPUS is phrased in the documents' own vocabulary, so routing looks
    better here than it will on questions real testers type.
    """
    from eval.calibrate_router import GENERAL, IN_CORPUS

    records = [
        {"id": f"corpus_{i:02d}", "question": q, "expects": "documents",
         "source_document": None}
        for i, q in enumerate(IN_CORPUS, 1)
    ]
    records += [
        {"id": f"general_{i:02d}", "question": q, "expects": "general",
         "source_document": None}
        for i, q in enumerate(GENERAL, 1)
    ]
    return records


def _load_question_set(input_file: Optional[Path]) -> tuple[list[dict], bool]:
    """Return (records, came_from_file)."""
    if input_file is None:
        return _default_question_set(), False

    if not input_file.exists():
        raise SystemExit(f"eval set not found: {input_file}")

    with open(input_file) as f:
        records = json.load(f)

    missing = [r.get("id", "?") for r in records if not r.get("question")]
    if missing:
        raise SystemExit(f"records with no question: {missing[:5]}")

    unlabelled = [r.get("id", "?") for r in records
                  if r.get("expects") not in ("documents", "general")]
    if unlabelled:
        raise SystemExit(
            f"records must set expects='documents' or 'general': {unlabelled[:5]}"
        )
    return records, True


# ── Transport ────────────────────────────────────────────────────────────────

def login() -> str:
    """Authenticate the same way the chat pipeline does — no password in this file."""
    token = get_settings().service_token
    if not token:
        raise SystemExit(
            "SERVICE_TOKEN is not set. This suite authenticates the same way the "
            "chat pipeline does; set it in .env and recreate the api container."
        )
    r = httpx.post(
        f"{BASE}/auth/service-token",
        headers={"X-Service-Token": token},
        json={"username": USER},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def ask(token: str, query: str, session_id: str | None = None) -> dict:
    payload: dict = {"messages": [{"role": "user", "content": query}]}
    if session_id:
        payload["session_id"] = session_id
    r = httpx.post(
        f"{BASE}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    body["_answer"] = body["choices"][0]["message"]["content"]
    return body


def _answered_from_documents(agent_type: str) -> bool:
    """
    Whether the answer was grounded in the corpus.

    'general_fallback' counts as NOT from documents on purpose: retrieval ran,
    the gate admitted the question, and the document answer was still discarded
    in favour of general knowledge. From the user's side that is the same
    outcome as never having searched.
    """
    return agent_type.startswith("document_rag")


# ── 1. Routing accuracy ──────────────────────────────────────────────────────

def measure_routing(records: list[dict], responses: dict[str, dict]) -> dict:
    console.print("\n[bold blue]1. Routing accuracy[/bold blue]")

    per_class: dict[str, dict[str, int]] = {
        "documents": {"correct": 0, "total": 0},
        "general": {"correct": 0, "total": 0},
    }
    misrouted: list[tuple[str, str, str]] = []

    for rec in records:
        resp = responses.get(rec["id"])
        if resp is None:
            continue
        expected = rec["expects"]
        agent = resp.get("agent_type", "unknown")
        actual = "documents" if _answered_from_documents(agent) else "general"

        per_class[expected]["total"] += 1
        if actual == expected:
            per_class[expected]["correct"] += 1
        else:
            misrouted.append((rec["question"], expected, agent))

    total = sum(c["total"] for c in per_class.values())
    correct = sum(c["correct"] for c in per_class.values())
    overall = correct / total if total else 0.0

    for label, counts in per_class.items():
        if counts["total"]:
            rate = counts["correct"] / counts["total"]
            console.print(
                f"   {label:<10} {counts['correct']}/{counts['total']} "
                f"({rate:.0%})"
            )
    console.print(f"   [bold]overall {correct}/{total} ({overall:.0%})[/bold]")

    for question, expected, agent in misrouted[:10]:
        console.print(f"     [yellow]miss[/yellow] expected {expected}, got "
                      f"{agent}: {question[:60]}")

    return {
        "overall": overall,
        "correct": correct,
        "total": total,
        "per_class": per_class,
        "misrouted": [
            {"question": q, "expected": e, "agent_type": a} for q, e, a in misrouted
        ],
    }


# ── 2. Abstention rate ───────────────────────────────────────────────────────

def measure_abstention(records: list[dict], responses: dict[str, dict]) -> dict:
    """
    Fraction of in-corpus questions that produced no usable document answer.

    Two distinct failures are counted, because both look identical to the user:
    an explicit "I couldn't find this" refusal, and an answer that arrived with
    no citations at all. Only questions labelled as covered by the corpus are
    considered — abstaining on a general question is correct behaviour.
    """
    console.print("\n[bold blue]2. Abstention rate (in-corpus questions)[/bold blue]")

    in_corpus = [r for r in records if r["expects"] == "documents"]
    abstained: list[dict] = []
    considered = 0

    for rec in in_corpus:
        resp = responses.get(rec["id"])
        if resp is None:
            continue
        considered += 1
        answer = resp.get("_answer", "")
        citations = resp.get("citations") or []
        agent = resp.get("agent_type", "unknown")

        reason = None
        if is_refusal(answer):
            reason = "explicit refusal"
        elif not _answered_from_documents(agent):
            reason = f"routed away to {agent}"
        elif not citations:
            reason = "no citations"

        if reason:
            abstained.append({
                "question": rec["question"],
                "reason": reason,
                "agent_type": agent,
            })

    rate = len(abstained) / considered if considered else 0.0
    console.print(
        f"   {len(abstained)}/{considered} in-corpus questions returned no "
        f"grounded answer ({rate:.0%})"
    )
    for item in abstained[:10]:
        console.print(f"     [yellow]{item['reason']}[/yellow]: "
                      f"{item['question'][:60]}")

    return {"rate": rate, "abstained": len(abstained), "considered": considered,
            "detail": abstained}


# ── 3. Recall@k ──────────────────────────────────────────────────────────────

def measure_recall(records: list[dict], responses: dict[str, dict]) -> dict:
    """
    Did the citations include the document the answer should have come from?

    Skipped rather than guessed when no record carries `source_document`.
    Inferring the expected document from what retrieval returned would measure
    retrieval against itself and always score 100%.
    """
    console.print("\n[bold blue]3. Recall@k (expected document cited)[/bold blue]")

    labelled = [r for r in records
                if r["expects"] == "documents" and r.get("source_document")]
    if not labelled:
        console.print(
            "   [yellow]skipped — no record supplies 'source_document'. This is "
            "the cheapest metric to unlock: one filename per question, no "
            "answers needed.[/yellow]"
        )
        return {"skipped": True, "reason": "no source_document labels"}

    hits, misses = 0, []
    for rec in labelled:
        resp = responses.get(rec["id"])
        if resp is None:
            continue
        expected = rec["source_document"].strip().lower()
        cited = {
            str(c.get("document_name", "")).strip().lower()
            for c in (resp.get("citations") or [])
        }
        if any(expected in name or name in expected for name in cited if name):
            hits += 1
        else:
            misses.append({
                "question": rec["question"],
                "expected": rec["source_document"],
                "cited": sorted(n for n in cited if n),
            })

    total = hits + len(misses)
    rate = hits / total if total else 0.0
    console.print(f"   {hits}/{total} cited the expected document ({rate:.0%})")
    for m in misses[:10]:
        console.print(f"     [yellow]miss[/yellow] wanted {m['expected']}, cited "
                      f"{m['cited'] or 'nothing'}: {m['question'][:50]}")

    return {"skipped": False, "rate": rate, "hits": hits, "total": total,
            "misses": misses}


# ── 4. Self-consistency ──────────────────────────────────────────────────────

def measure_consistency(
    token: str,
    records: list[dict],
    repeats: int,
    sample: int,
) -> dict:
    """
    Ask the same question several times and measure how far the answers drift.

    Similarity is mean pairwise cosine distance between answer embeddings, using
    the project's own embedding model — so this needs no judge and no labels, and
    is fully deterministic given the answers.

    Read it as a floor, not a score. LLM_TEMPERATURE is normally 0.1, and the
    chat endpoint takes no per-request temperature, so answers are expected to be
    near-identical. That makes a high number unremarkable and a LOW number very
    informative: drift at temperature 0.1 means the question is genuinely
    unstable — usually retrieval returning a different passage set each time.
    """
    settings = get_settings()
    console.print(
        f"\n[bold blue]4. Self-consistency ({repeats}x on {sample} questions, "
        f"temperature {settings.llm_temperature})[/bold blue]"
    )

    from services.embedding_service import embed_texts

    candidates = [r for r in records if r["expects"] == "documents"][:sample]
    if not candidates:
        console.print("   [yellow]skipped — no in-corpus questions[/yellow]")
        return {"skipped": True, "reason": "no in-corpus questions"}

    per_question: list[dict] = []
    for rec in candidates:
        answers: list[str] = []
        for n in range(repeats):
            # A fresh session each time, or turn 2 would see turn 1 in history
            # and the run would measure follow-up handling instead of stability.
            resp = ask(token, rec["question"],
                       session_id=f"consistency-{uuid.uuid4().hex[:8]}")
            answers.append(resp.get("_answer", ""))

        usable = [a for a in answers if a.strip()]
        if len(usable) < 2:
            per_question.append({
                "question": rec["question"],
                "similarity": None,
                "note": "fewer than two non-empty answers",
            })
            continue

        vectors = embed_texts(usable)
        sims = [_cosine(a, b) for a, b in combinations(vectors, 2)]
        mean_sim = statistics.fmean(sims)
        per_question.append({
            "question": rec["question"],
            "similarity": mean_sim,
            "min_pairwise": min(sims),
        })
        colour = "green" if mean_sim >= 0.95 else "yellow" if mean_sim >= 0.85 else "red"
        console.print(f"   [{colour}]{mean_sim:.3f}[/{colour}]  "
                      f"{rec['question'][:60]}")

    scored = [p["similarity"] for p in per_question if p["similarity"] is not None]
    overall = statistics.fmean(scored) if scored else 0.0
    console.print(f"   [bold]mean similarity {overall:.3f}[/bold]")

    return {"skipped": False, "mean_similarity": overall, "repeats": repeats,
            "per_question": per_question}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Runner ───────────────────────────────────────────────────────────────────

def run(
    input_file: Optional[Path],
    output_file: Path,
    repeats: int,
    consistency_sample: int,
) -> None:
    """Measure everything that can be measured without human-written answers."""
    records, from_file = _load_question_set(input_file)
    token = login()

    settings = get_settings()
    if settings.router_band_is_degenerate:
        console.print(
            f"[red]WARNING: ROUTER_RAG_SCORE_HIGH "
            f"({settings.router_rag_score_high}) <= LOW "
            f"({settings.router_rag_score_low}), so blended answers are "
            f"impossible. Routing numbers below describe a two-mode system, not "
            f"the three-mode one intended.[/red]"
        )

    console.print(
        f"[yellow]Asking {len(records)} questions "
        f"({'from ' + str(input_file) if from_file else 'from calibrate_router'})"
        f"...[/yellow]"
    )

    responses: dict[str, dict] = {}
    for rec in records:
        try:
            responses[rec["id"]] = ask(
                token, rec["question"],
                session_id=f"accuracy-{uuid.uuid4().hex[:8]}",
            )
        except Exception as e:
            console.print(f"   [red]request failed[/red] {rec['id']}: {e}")

    results = {
        "question_count": len(records),
        "answered": len(responses),
        "source": str(input_file) if from_file else "eval/calibrate_router.py",
        "routing": measure_routing(records, responses),
        "abstention": measure_abstention(records, responses),
        "recall_at_k": measure_recall(records, responses),
    }

    if consistency_sample > 0 and repeats > 1:
        results["self_consistency"] = measure_consistency(
            token, records, repeats, consistency_sample
        )
    else:
        console.print("\n[yellow]4. Self-consistency skipped by flag[/yellow]")
        results["self_consistency"] = {"skipped": True, "reason": "disabled by flag"}

    _print_summary(results, from_file)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    console.print(f"\n[green]Results saved to {output_file}[/green]")


def _print_summary(results: dict, from_file: bool) -> None:
    table = Table(title="Accuracy suite (no ground truth required)", show_header=True)
    table.add_column("Measure", style="cyan")
    table.add_column("Result", style="green")
    table.add_column("Reads as", style="white")

    r = results["routing"]
    table.add_row("Routing accuracy", f"{r['overall']:.0%} ({r['correct']}/{r['total']})",
                  "document questions reached documents, general reached general")

    a = results["abstention"]
    table.add_row("Abstention rate", f"{a['rate']:.0%} ({a['abstained']}/{a['considered']})",
                  "covered questions that got no grounded answer — lower is better")

    rec = results["recall_at_k"]
    if rec.get("skipped"):
        table.add_row("Recall@k", "not measured", str(rec.get("reason", "")))
    else:
        table.add_row("Recall@k", f"{rec['rate']:.0%} ({rec['hits']}/{rec['total']})",
                      "expected source document appeared in the citations")

    sc = results["self_consistency"]
    if sc.get("skipped"):
        table.add_row("Self-consistency", "not measured", str(sc.get("reason", "")))
    else:
        table.add_row("Self-consistency", f"{sc['mean_similarity']:.3f}",
                      f"answer drift across {sc['repeats']} repeats — low is a red flag")

    console.print()
    console.print(table)

    console.print(
        "\n[yellow]None of the above measures correctness.[/yellow] They show the "
        "system routes, cites and behaves consistently. Whether an answer is "
        "actually right still needs someone who knows the documents to confirm "
        "it — budget roughly 30 questions. Run eval/ragas_runner.py alongside "
        "this for faithfulness and answer relevance."
    )
    if not from_file:
        console.print(
            "[yellow]Question set came from eval/calibrate_router.py, whose "
            "in-corpus questions are phrased in the documents' own vocabulary. "
            "Routing therefore scores better here than it will on questions real "
            "testers type. Rewrite them against the real corpus and pass "
            "--input.[/yellow]"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.accuracy_suite",
        description="Accuracy measurements that need no human-written answers.",
    )
    parser.add_argument(
        "-i", "--input", dest="input_file", type=Path, default=None,
        help="Eval set JSON. Defaults to the labelled sets in "
             "eval/calibrate_router.py.",
    )
    parser.add_argument(
        "-o", "--output", dest="output_file", type=Path,
        default=Path("eval/accuracy_results.json"),
        help="Where to write the results JSON",
    )
    parser.add_argument(
        "--repeats", type=int, default=5,
        help="Self-consistency runs per sampled question (default 5)",
    )
    parser.add_argument(
        "--consistency-sample", type=int, default=5,
        help="In-corpus questions to test for consistency, 0 to skip (default 5)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    run(args.input_file, args.output_file, args.repeats, args.consistency_sample)


if __name__ == "__main__":
    main()
