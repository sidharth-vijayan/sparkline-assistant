"""
eval/ragas_runner.py
─────────────────────
RAGAS evaluation runner — measures answer quality on an eval set.

Metrics, split by what they need:

  Reference-free (no human-written answer required):
    - Faithfulness:     is every claim in the answer supported by the retrieved
                        context? This is the hallucination rate.
    - Answer Relevance: does the answer address the question actually asked?

  Reference-based (needs `ground_truth` written by someone who knows the docs):
    - Context Precision: were the retrieved chunks the ones that matter?

If no entry in the eval set carries a `ground_truth`, only the reference-free
metrics run, and the report says so. That is the normal case before subject
experts have confirmed answers, and it is deliberately not treated as an error —
faithfulness alone is worth measuring.

Usage:
  python -m eval.ragas_runner --input eval/golden_set.json --output eval/results.json

──────────────────────────────────────────────────────────────────────────────
LOCAL-ONLY, ENFORCED RATHER THAN ASSERTED

RAGAS is an LLM-as-judge framework: it calls a model to grade each answer. Left
to itself it uses OpenAI, so an earlier version of this file called
`evaluate(dataset, metrics=...)` with no judge configured while its own
docstring claimed evaluation was local. Had the packages been installed and an
OPENAI_API_KEY been present, every eval question, every generated answer and
every retrieved document chunk would have been sent to OpenAI.

The judge and the embeddings are therefore now passed explicitly, pointed at the
same Ollama that serves the application, and `_require_local_judge()` refuses to
run at all if LLM_BASE_URL is not a local address. Neither the default nor an
ambient API key can put company documents on the network.

Note on interpretation: the judge is `LLM_MODEL_NAME`, the same model being
graded. It shares the system's blind spots and will accept its own mistakes, so
these scores are directional. Pair them with a human read of ~20 answers.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import structlog
from rich.console import Console
from rich.table import Table

# argparse rather than Typer — see the note in admin_tools/ingest_cli.py: with
# click 8.2+ installed, every Typer CLI here failed before parsing a flag.
logger = structlog.get_logger(__name__)
console = Console()

_INSTALL_HINT = (
    "Missing evaluation dependencies. Install them with:\n"
    '  pip install "ragas>=0.2,<0.3" "datasets>=2.19" "langchain-openai>=0.1"'
)

# Hostnames that mean "this machine or this docker network". Anything else is
# treated as off-box and refused.
_LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "ollama",
    "sparkline_ollama",
}


def _require_local_judge(base_url: str) -> None:
    """
    Refuse to evaluate unless the judge is served locally.

    The check is on the host, not on whether a key happens to be set: a key can
    be added to the environment later by someone with no idea this file exists,
    and the failure mode is silent exfiltration of document text rather than an
    error. Fail closed on the address instead.
    """
    host = (urlparse(base_url).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to run: LLM_BASE_URL points at '{host}', which is not a "
            f"local address. RAGAS sends every question, generated answer and "
            f"retrieved document chunk to the judge model, so evaluation must "
            f"only ever run against a model on this machine. Recognised local "
            f"hosts: {', '.join(sorted(_LOCAL_HOSTS))}."
        )


def _build_local_judge(settings: Any):
    """Wrap the local Ollama endpoint as a RAGAS judge."""
    try:
        from langchain_openai import ChatOpenAI
        from ragas.llms import LangchainLLMWrapper
    except ImportError as e:
        raise SystemExit(_INSTALL_HINT) from e

    # base_url overrides the OpenAI default, so nothing leaves the machine.
    # api_key must be non-empty for the client to construct; Ollama ignores it.
    return LangchainLLMWrapper(
        ChatOpenAI(
            model=settings.llm_model_name,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "ollama",
            temperature=0.0,
            timeout=settings.llm_timeout_seconds,
        )
    )


def _build_local_embeddings():
    """
    Wrap the project's own embedding model for RAGAS.

    Answer-relevance needs embeddings, and RAGAS would otherwise call OpenAI for
    them. Reusing services/embedding_service means no second model to pull and
    no second device to budget VRAM for — it is the same BGE instance the
    retrieval path already loads.
    """
    try:
        from langchain_core.embeddings import Embeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
    except ImportError as e:
        raise SystemExit(_INSTALL_HINT) from e

    from services.embedding_service import embed_query, embed_texts

    class _SparklineEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return embed_texts(texts)

        def embed_query(self, text: str) -> list[float]:
            return embed_query(text)

    return LangchainEmbeddingsWrapper(_SparklineEmbeddings())


def run_evaluation(
    input_file: Path,
    output_file: Path,
    limit: Optional[int],
) -> None:
    """Run RAGAS evaluation with a strictly local judge."""
    asyncio.run(_run_evaluation(input_file, output_file, limit))


async def _run_evaluation(
    input_file: Path,
    output_file: Path,
    limit: Optional[int],
) -> None:
    from config.settings import get_settings
    settings = get_settings()

    _require_local_judge(settings.llm_base_url)

    if not input_file.exists():
        console.print(f"[red]Eval set not found: {input_file}[/red]")
        raise SystemExit(1)

    with open(input_file) as f:
        eval_set = json.load(f)

    if limit:
        eval_set = eval_set[:limit]

    if not eval_set:
        console.print("[red]Eval set is empty — nothing to measure.[/red]")
        raise SystemExit(1)

    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness
    except ImportError:
        console.print(f"[red]{_INSTALL_HINT}[/red]")
        raise SystemExit(1)

    console.print(
        f"[yellow]Judge: {settings.llm_model_name} at {settings.llm_base_url} "
        f"(local)[/yellow]"
    )
    console.print(f"[yellow]Generating answers for {len(eval_set)} items...[/yellow]")

    eval_data: dict[str, list] = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
    }

    import httpx
    from gateway.middleware.auth import create_access_token

    eval_token = create_access_token(
        user_id="00000000-0000-0000-0000-000000000000", username="eval"
    )

    failures = 0
    async with httpx.AsyncClient(
        timeout=300, base_url=f"http://localhost:{settings.api_port}"
    ) as client:
        for item in eval_set:
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {eval_token}"},
                    json={
                        "messages": [{"role": "user", "content": item["question"]}],
                        "session_id": f"eval_{item.get('id', 'x')}",
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    answer = result["choices"][0]["message"]["content"]
                    contexts = [
                        c.get("chunk_text_preview", "")
                        for c in result.get("citations", [])
                    ]
                else:
                    logger.warning("eval.request_status", status=resp.status_code)
                    answer, contexts = "", []
                    failures += 1
            except Exception as e:
                logger.warning("eval.request_failed", error=str(e))
                answer, contexts = "", []
                failures += 1

            eval_data["question"].append(item["question"])
            eval_data["answer"].append(answer)
            eval_data["contexts"].append(contexts or [""])
            eval_data["ground_truth"].append(item.get("ground_truth", "") or "")

    if failures:
        console.print(
            f"[red]{failures} of {len(eval_set)} questions returned no answer. "
            f"Scores below are computed over blanks for those and will read "
            f"low for reasons unrelated to quality.[/red]"
        )

    # Only score what the data can actually support. Grading context precision
    # against a blank reference would produce a number with no meaning, which is
    # worse than reporting fewer metrics.
    has_references = any(g.strip() for g in eval_data["ground_truth"])
    metrics = [faithfulness, answer_relevancy]
    if has_references:
        metrics.append(context_precision)
    else:
        console.print(
            "[yellow]No ground_truth values in the eval set — running "
            "reference-free metrics only (faithfulness, answer relevancy). "
            "Context precision needs confirmed answers from someone who knows "
            "the documents.[/yellow]"
        )
        eval_data.pop("ground_truth")

    dataset = Dataset.from_dict(eval_data)

    console.print("[yellow]Scoring with the local judge...[/yellow]")
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=_build_local_judge(settings),
        embeddings=_build_local_embeddings(),
    )

    scores = {m.name: float(result[m.name]) for m in metrics}

    table = Table(title="RAGAS Evaluation Results", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Score", style="green")
    for metric, score in scores.items():
        color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
        table.add_row(metric, f"[{color}]{score:.3f}[/{color}]")
    console.print(table)

    console.print(
        "[yellow]Judge is the model under test, so these are directional. "
        "Pair with a human read of ~20 answers before quoting them.[/yellow]"
    )

    output = {
        "eval_set_size": len(eval_set),
        "unanswered": failures,
        "judge_model": settings.llm_model_name,
        "judge_base_url": settings.llm_base_url,
        "reference_based_metrics_included": has_references,
        "scores": scores,
        "individual_results": result.to_pandas().to_dict(orient="records"),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    console.print(f"[green]Results saved to {output_file}[/green]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.ragas_runner",
        description="RAGAS evaluation with a judge that is verified to be local.",
    )
    parser.add_argument("-i", "--input", dest="input_file", type=Path,
                        default=Path("eval/golden_set.json"),
                        help="Path to the eval set JSON")
    parser.add_argument("-o", "--output", dest="output_file", type=Path,
                        default=Path("eval/results.json"),
                        help="Path to write results JSON")
    parser.add_argument("-n", "--limit", type=int, default=None,
                        help="Limit the number of eval items")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    run_evaluation(args.input_file, args.output_file, args.limit)


if __name__ == "__main__":
    main()
