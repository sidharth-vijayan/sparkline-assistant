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
  python -m admin_tools.ingest_cli set-access <document-id> --departments "HR"
  python -m admin_tools.ingest_cli rebuild-bm25

Requires SPARKLINE_API_URL and SPARKLINE_ADMIN_PASSWORD set in environment
(or the .env file). Without the password it prompts.

Built on argparse rather than Typer on purpose. Typer 0.12 calls
click.Parameter.make_metavar() with the old signature, and click 8.2 changed it,
so with the versions actually installed here every Typer CLI in this repo raised
`TypeError: Parameter.make_metavar() missing 1 required positional argument`
before parsing a single flag — including this one, which is the first step of
ingesting the tester documents. Pinning click would fix it until the next
resolve, and would need a rebuilt image to reach the container. argparse is in
the standard library, cannot skew, and is more than enough for four commands.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.table import Table

console = Console()

API_URL = os.getenv("SPARKLINE_API_URL", "http://localhost:8000")
ADMIN_USERNAME = os.getenv("SPARKLINE_ADMIN_USERNAME", "file.admin")
# No default. This account can ingest and withdraw documents for every user, so
# a password shipped in the source is a password everyone with repo access has.
ADMIN_PASSWORD = os.getenv("SPARKLINE_ADMIN_PASSWORD")


def _get_token() -> str:
    """Authenticate and return a JWT token."""
    password = ADMIN_PASSWORD
    if not password:
        # Prompting keeps the CLI usable without putting the secret in the
        # shell history or the environment of every other process.
        password = getpass.getpass(f"Password for {ADMIN_USERNAME}: ")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{API_URL}/auth/login",
            json={"username": ADMIN_USERNAME, "password": password},
        )
        if resp.status_code != 200:
            console.print(f"[red]Login failed: {resp.text}[/red]")
            raise SystemExit(1)
        return resp.json()["access_token"]


def cmd_upload(args: argparse.Namespace) -> None:
    """Upload a document and tag it with access control metadata."""
    file_path: Path = args.file_path
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        raise SystemExit(1)

    console.print("[yellow]Authenticating...[/yellow]")
    token = _get_token()

    console.print(f"[yellow]Uploading {file_path.name}...[/yellow]")
    file_bytes = file_path.read_bytes()

    data = {"is_public": "true" if args.public else "false"}
    if args.departments:
        data["allowed_departments"] = args.departments
    if args.designations:
        data["allowed_designations"] = args.designations

    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{API_URL}/admin/ingest",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_path.name, file_bytes, "application/octet-stream")},
            data=data,
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print("[green]Ingested successfully.[/green]")
        console.print(f"   Document ID:    {result['document_id']}")
        console.print(f"   Version:        v{result['version_number']}")
        console.print(f"   Chunks created: {result['chunks_created']}")
        console.print(f"   Public:         {result['is_public']}")
        console.print(f"   Departments:    {result['allowed_departments'] or 'All'}")
        console.print(f"   Designations:   {result['allowed_designations'] or 'All'}")
        if result.get("truncated"):
            console.print(f"[yellow]   Truncated: {result['truncated']}[/yellow]")
    else:
        console.print(f"[red]Ingestion failed: {resp.status_code} — {resp.text}[/red]")
        raise SystemExit(1)


def cmd_list(args: argparse.Namespace) -> None:
    """List all ingested documents."""
    token = _get_token()

    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{API_URL}/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        console.print(f"[red]Failed: {resp.text}[/red]")
        raise SystemExit(1)

    documents = resp.json()
    if not documents:
        console.print("[yellow]No documents ingested yet.[/yellow]")
        return

    table = Table(title="Ingested Documents", show_header=True)
    table.add_column("Document ID", style="dim")
    table.add_column("Filename", style="cyan")
    table.add_column("Public", style="green")
    table.add_column("Departments")
    table.add_column("Designations")
    table.add_column("Updated At")

    for doc in documents:
        table.add_row(
            doc["document_id"],
            doc["filename"],
            "yes" if doc["is_public"] else "no",
            ", ".join(doc["allowed_departments"] or []) or "All",
            ", ".join(doc["allowed_designations"] or []) or "All",
            doc["updated_at"][:10],
        )

    console.print(table)
    console.print(
        "[dim]Change permissions without re-uploading:  "
        "set-access <document-id> --departments \"HR,Finance\"[/dim]"
    )


def cmd_set_access(args: argparse.Namespace) -> None:
    """
    Change who may see an already-ingested document.

    The reason this exists: the pilot corpus is ingested before HR supplies the
    department list, so documents start untagged and have to be labelled later.
    Without this the only way to correct a tag was to withdraw the document and
    re-upload it, which re-parses and re-embeds the whole file to change three
    fields — and now also deletes the stored original.
    """
    token = _get_token()

    body: dict = {}
    if args.departments is not None:
        body["allowed_departments"] = (
            [d.strip() for d in args.departments.split(",") if d.strip()] or None
        )
    if args.designations is not None:
        body["allowed_designations"] = (
            [d.strip() for d in args.designations.split(",") if d.strip()] or None
        )
    if args.public is not None:
        body["is_public"] = args.public

    if not body:
        console.print(
            "[red]Nothing to change. Pass --departments, --designations, "
            "--public or --not-public.[/red]"
        )
        raise SystemExit(1)

    with httpx.Client(timeout=60) as client:
        resp = client.patch(
            f"{API_URL}/admin/documents/{args.document_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print(f"[green]{result['message']}[/green]")
        console.print(f"   Public:       {result['is_public']}")
        console.print(f"   Departments:  {result['allowed_departments'] or 'All'}")
        console.print(f"   Designations: {result['allowed_designations'] or 'All'}")
        console.print(f"   Changed:      {', '.join(result['updated_fields'])}")
    else:
        console.print(f"[red]Failed: {resp.status_code} — {resp.text}[/red]")
        raise SystemExit(1)


def cmd_delete(args: argparse.Namespace) -> None:
    """
    Withdraw a document and delete its stored file.

    Confirmation is required and not skippable by a flag. Withdrawal now purges
    the original from MinIO as well as the index, so there is nothing left to
    restore from — the only recovery is having the file elsewhere.
    """
    token = _get_token()

    with httpx.Client(timeout=30) as client:
        listing = client.get(
            f"{API_URL}/admin/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
    name = args.document_id
    if listing.status_code == 200:
        for doc in listing.json():
            if doc["document_id"] == args.document_id:
                name = doc["filename"]
                break

    console.print(
        f"[red]This permanently deletes '{name}' and its stored file. "
        f"It cannot be undone.[/red]"
    )
    if input("Type the word DELETE to confirm: ").strip() != "DELETE":
        console.print("[yellow]Cancelled — nothing was removed.[/yellow]")
        return

    with httpx.Client(timeout=120) as client:
        resp = client.delete(
            f"{API_URL}/admin/documents/{args.document_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print(f"[green]{result['message']}[/green]")
        console.print(f"   Versions removed: {result['versions_removed']}")
        console.print(f"   Chunks removed:   {result['chunks_removed']}")
        console.print(f"   Files deleted:    {result.get('files_removed', 0)}")
    else:
        console.print(f"[red]Failed: {resp.status_code} — {resp.text}[/red]")
        raise SystemExit(1)


def cmd_rebuild_bm25(args: argparse.Namespace) -> None:
    """Manually trigger a BM25 index rebuild."""
    token = _get_token()

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{API_URL}/admin/rebuild-bm25",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 200:
        result = resp.json()
        console.print(
            f"[green]BM25 rebuilt. Corpus size: {result['corpus_size']} chunks[/green]"
        )
    else:
        console.print(f"[red]Failed: {resp.text}[/red]")
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m admin_tools.ingest_cli",
        description="Sparkline document ingestion CLI (file-admin only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("upload", help="Upload and tag a document")
    up.add_argument("file_path", type=Path, help="Path to the document to upload")
    up.add_argument("-d", "--departments",
                    help="Comma-separated department names, e.g. 'HR,Finance'")
    up.add_argument("-r", "--designations",
                    help="Comma-separated designation names, e.g. 'Manager,Director'")
    up.add_argument("-p", "--public", action="store_true",
                    help="Make the document readable by all users")
    up.set_defaults(func=cmd_upload)

    ls = sub.add_parser("list", help="List all ingested documents")
    ls.set_defaults(func=cmd_list)

    sa = sub.add_parser("set-access",
                        help="Change permissions on an existing document")
    sa.add_argument("document_id", help="Document ID (see the `list` command)")
    sa.add_argument("-d", "--departments",
                    help="Comma-separated departments. Pass '' to clear.")
    sa.add_argument("-r", "--designations",
                    help="Comma-separated designations. Pass '' to clear.")
    sa.add_argument("-p", "--public", dest="public", action="store_true",
                    default=None, help="Make readable by all users")
    sa.add_argument("--not-public", dest="public", action="store_false",
                    help="Restrict again to the tagged departments/designations")
    sa.set_defaults(func=cmd_set_access)

    dl = sub.add_parser("delete",
                        help="Withdraw a document AND delete its stored file")
    dl.add_argument("document_id", help="Document ID (see the `list` command)")
    dl.set_defaults(func=cmd_delete)

    rb = sub.add_parser("rebuild-bm25", help="Trigger a BM25 index rebuild")
    rb.set_defaults(func=cmd_rebuild_bm25)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
