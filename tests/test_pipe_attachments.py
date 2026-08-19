"""
tests/test_pipe_attachments.py
───────────────────────────────
Tests for the pipe's reading of Open WebUI's attachment payloads.

This is the part most likely to fail silently. If the shape is misread the pipe
simply uploads nothing, the user sees an ordinary answer, and there is no error
anywhere to notice. So the shapes Open WebUI 0.11.0 actually sends are pinned
here as fixtures.

A chat file upload arrives as type "text" with the real data nested under
"file" — verified against the running container's retrieval/utils.py, which
reads item["file"]["data"]["content"].

    poetry run pytest tests/test_pipe_attachments.py
"""

import importlib.util
import pathlib

import pytest

# The pipe is a standalone script for Open WebUI, not part of the package.
_spec = importlib.util.spec_from_file_location(
    "sparkline_pipeline",
    pathlib.Path(__file__).parent.parent / "open_webui_pipeline" / "sparkline_pipeline.py",
)
_pipe_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pipe_module)
Pipe = _pipe_module.Pipe


def nested_upload(file_id="f-1", name="budget.xlsx", path="/app/backend/data/uploads/f-1_budget.xlsx",
                  content="Sheet1 | A | B"):
    """The shape a temporary chat upload actually arrives in."""
    return {
        "type": "text",
        "id": file_id,
        "name": name,
        "context": "full",
        "file": {
            "id": file_id,
            "filename": name,
            "path": path,
            "data": {"content": content},
            "meta": {"name": name, "content_type": "application/vnd.ms-excel"},
        },
    }


def test_reads_the_id_and_name_from_a_chat_upload():
    info = Pipe._file_info(nested_upload())

    assert info["id"] == "f-1"
    assert info["name"] == "budget.xlsx"


def test_reads_the_raw_path_so_our_own_parsers_can_be_used():
    """Open WebUI's extracted text loses spreadsheet structure. The raw file is
    on disk in this container, so prefer it."""
    info = Pipe._file_info(nested_upload())

    assert info["path"] == "/app/backend/data/uploads/f-1_budget.xlsx"


def test_keeps_the_extracted_text_as_a_fallback():
    info = Pipe._file_info(nested_upload())

    assert info["content"] == "Sheet1 | A | B"


def test_handles_a_flat_entry_without_a_nested_file():
    info = Pipe._file_info({"type": "file", "id": "f-2", "name": "notes.txt",
                            "content": "hello"})

    assert info["id"] == "f-2"
    assert info["name"] == "notes.txt"
    assert info["content"] == "hello"


def test_falls_back_to_the_meta_name_when_filename_is_absent():
    item = nested_upload()
    del item["name"]
    del item["file"]["filename"]

    info = Pipe._file_info(item)

    assert info["name"] == "budget.xlsx"


def test_ignores_images():
    """An image has nothing our parsers can read, and the vision path is not
    ours to intercept."""
    assert Pipe._file_info({"type": "image", "id": "i-1", "url": "data:..."}) is None


def test_ignores_an_entry_with_no_identifier():
    assert Pipe._file_info({"type": "text", "name": "orphan.txt"}) is None


def test_ignores_an_entry_with_neither_a_path_nor_content():
    """Nothing to upload — must be skipped rather than sent as an empty file."""
    item = nested_upload(path=None, content=None)

    assert Pipe._file_info(item) is None


def test_a_collection_reference_without_content_is_skipped():
    assert Pipe._file_info({"type": "collection", "id": "c-1"}) is None


# ── Deduplication ─────────────────────────────────────────────────────────

def test_skips_files_the_chat_already_holds():
    """Open WebUI re-sends the chat's whole file list on every message, so
    without this the same document is re-embedded on every single turn."""
    items = [nested_upload("f-1"), nested_upload("f-2"), nested_upload("f-3")]

    pending = Pipe._pending_attachments(items, already_held={"f-1", "f-3"})

    assert [i["id"] for i in pending] == ["f-2"]


def test_everything_is_pending_when_the_chat_holds_nothing():
    items = [nested_upload("f-1"), nested_upload("f-2")]

    pending = Pipe._pending_attachments(items, already_held=set())

    assert [i["id"] for i in pending] == ["f-1", "f-2"]


def test_nothing_is_pending_when_the_chat_holds_them_all():
    items = [nested_upload("f-1")]

    assert Pipe._pending_attachments(items, already_held={"f-1"}) == []


def test_unreadable_entries_are_dropped_rather_than_queued():
    items = [nested_upload("f-1"), {"type": "image", "id": "i-1"}]

    pending = Pipe._pending_attachments(items, already_held=set())

    assert [i["id"] for i in pending] == ["f-1"]


def test_no_files_at_all_is_not_an_error():
    assert Pipe._pending_attachments([], already_held=set()) == []
    assert Pipe._pending_attachments(None, already_held=set()) == []
