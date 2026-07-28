"""
admin_tools/ingest_cli.py
──────────────────────────
CLI for the file-admin person to upload and tag documents.

Usage:
  python -m admin_tools.ingest_cli upload path/to/document.pdf \\
      --departments "HR,Finance" \\
      --designations "Manager,Director" \\
      --public

  python -m admin_tools.ingest_cli list

Requires SPARKLINE_API_URL and SPARKLINE_ADMIN_PASSWORD set in environment
(or the .env file).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="sparkline-admin", help="Sparkline document ingestion CLI")
console = Console()

API_URL = os.getenv("SPARKLINE_API_URL", "http://localhost:8000")
ADMIN_USERNAME = os.getenv("SPARKLINE_ADMIN_USERNAME", "file.admin")
ADMIN_PASSWORD = os.getenv("SPARKLINE_ADMIN_PASSWORD", "FileAdmin@2025")


def _get_token() -> str:
    """Authenticate and return a JWT token."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{API_URL}/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        if resp.status_code != 200:
            console.print(f"[red]Login failed: {resp.text}[/red]")
            raise typer.Exit(1)
        return resp.json()["access_token"]


@app.command("upload")
def upload_document(
    file_path: Path = typer.Argument(..., help="Path to the document to upload"),
    departments: Optional[str] = typer.Option(
        None,
        "--departments", "-d",
        help="Comma-separated department names (e.g. 'HR,Finance')",
    ),
    designations: Optional[str] = typer.Option(
        None,
        "--designations", "-r",
        help="Comma-separated designation names (e.g. 'Manager,Director')",
    ),
    public: bool = typer.Option(
        False,
        "--public", "-p",
        is_flag=True,
        help="Make the document accessible to all users",
    ),
) -> None:
    """Upload a document and tag it with access control metadata."""
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(1)

    console.print(f"[yellow]Authenticating...[/yellow]")
    token = _get_token()

    console.print(f"[yellow]Uploading {file_path.name}...[/yellow]")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    data = {"is_public": "true" if public else "false"}
    if departments:
        data["allowed_departments"] = departments
    if designations:
        data["allowed_designations"] = designations

    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{API_URL}/admin/ingest",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_path.name, file_bytes, "application/octet-stream")},
            data=data,
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print(f"[green]✅ Ingested successfully![/green]")
        console.print(f"   Document ID:    {result['document_id']}")
        console.print(f"   Version:        v{result['version_number']}")
        console.print(f"   Chunks created: {result['chunks_created']}")
        console.print(f"   Public:         {result['is_public']}")
        console.print(f"   Departments:    {result['allowed_departments'] or 'All'}")
        console.print(f"   Designations:   {result['allowed_designations'] or 'All'}")
    else:
        console.print(f"[red]Ingestion failed: {resp.status_code} — {resp.text}[/red]")
        raise typer.Exit(1)


@app.command("list")
def list_documents() -> None:
    """List all ingested documents."""
    token = _get_token()

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{API_URL}/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        console.print(f"[red]Failed: {resp.text}[/red]")
        raise typer.Exit(1)

    documents = resp.json()
    if not documents:
        console.print("[yellow]No documents ingested yet.[/yellow]")
        return

    table = Table(title="Ingested Documents", show_header=True)
    table.add_column("Filename", style="cyan")
    table.add_column("Public", style="green")
    table.add_column("Departments")
    table.add_column("Designations")
    table.add_column("Updated At")

    for doc in documents:
        table.add_row(
            doc["filename"],
            "✓" if doc["is_public"] else "✗",
            ", ".join(doc["allowed_departments"] or []) or "All",
            ", ".join(doc["allowed_designations"] or []) or "All",
            doc["updated_at"][:10],
        )

    console.print(table)


@app.command("rebuild-bm25")
def rebuild_bm25_index() -> None:
    """Manually trigger a BM25 index rebuild."""
    token = _get_token()

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{API_URL}/admin/rebuild-bm25",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print(f"[green]BM25 rebuilt. Corpus size: {result['corpus_size']} chunks[/green]")
    else:
        console.print(f"[red]Failed: {resp.text}[/red]")


if __name__ == "__main__":
    app()
