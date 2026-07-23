"""
eval/ragas_runner.py
─────────────────────
RAGAS evaluation runner — measures RAG quality metrics on a golden eval set.

Metrics:
  - Faithfulness:       Is the answer grounded in the retrieved context?
  - Answer Relevance:   Does the answer address the actual question?
  - Context Precision:  Are the retrieved chunks relevant to the question?

Usage:
  python -m eval.ragas_runner --input eval/golden_set.json --output eval/results.json

The golden set is a JSON file with the format defined in eval/golden_set.json.
RAGAS uses a local LLM (via LLM_BASE_URL) for evaluation — no external APIs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.table import Table

logger = structlog.get_logger(__name__)
console = Console()
app = typer.Typer()


@app.command()
def run_evaluation(
    input_file: Path = typer.Option(
        Path("eval/golden_set.json"),
        "--input", "-i",
        help="Path to golden eval set JSON",
    ),
    output_file: Path = typer.Option(
        Path("eval/results.json"),
        "--output", "-o",
        help="Path to write results JSON",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", help="Limit number of eval items"
    ),
) -> None:
    """Run RAGAS evaluation on the golden eval set."""
    asyncio.run(_run_evaluation(input_file, output_file, limit))


async def _run_evaluation(
    input_file: Path,
    output_file: Path,
    limit: Optional[int],
) -> None:
    from config.settings import get_settings
    settings = get_settings()

    if not input_file.exists():
        console.print(f"[red]Golden set not found: {input_file}[/red]")
        raise typer.Exit(1)

    with open(input_file) as f:
        golden_set = json.load(f)

    if limit:
        golden_set = golden_set[:limit]

    console.print(f"[yellow]Running RAGAS on {len(golden_set)} eval items...[/yellow]")

    # Import RAGAS with local LLM
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision
        from datasets import Dataset
    except ImportError:
        console.print("[red]RAGAS not installed. Run: pip install ragas datasets[/red]")
        raise typer.Exit(1)

    # Build dataset from golden set
    eval_data = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
    }

    console.print("[yellow]Generating RAG answers from the system...[/yellow]")

    import httpx
    from gateway.middleware.auth import create_access_token

    # Use the file admin token for evaluation
    eval_token = create_access_token(user_id="00000000-0000-0000-0000-000000000000", username="eval")

    async with httpx.AsyncClient(timeout=120, base_url=f"http://localhost:{settings.api_port}") as client:
        for item in golden_set:
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
                    # Extract context from citations
                    contexts = [
                        c.get("chunk_text_preview", "")
                        for c in result.get("citations", [])
                    ]
                else:
                    answer = ""
                    contexts = []
            except Exception as e:
                logger.warning("eval.request_failed", error=str(e))
                answer = ""
                contexts = []

            eval_data["question"].append(item["question"])
            eval_data["answer"].append(answer)
            eval_data["contexts"].append(contexts or [""])
            eval_data["ground_truth"].append(item.get("ground_truth", ""))

    dataset = Dataset.from_dict(eval_data)

    console.print("[yellow]Running RAGAS metrics...[/yellow]")
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision],
    )

    # Display results
    table = Table(title="RAGAS Evaluation Results", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Score", style="green")

    scores = {
        "faithfulness": float(result["faithfulness"]),
        "answer_relevancy": float(result["answer_relevancy"]),
        "context_precision": float(result["context_precision"]),
    }

    for metric, score in scores.items():
        color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
        table.add_row(metric, f"[{color}]{score:.3f}[/{color}]")

    console.print(table)

    # Save results
    output = {
        "eval_set_size": len(golden_set),
        "scores": scores,
        "individual_results": result.to_pandas().to_dict(orient="records"),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)

    console.print(f"[green]Results saved to {output_file}[/green]")


if __name__ == "__main__":
    app()
