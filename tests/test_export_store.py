"""
tests/test_export_store.py
───────────────────────────
Tests for storing and retrieving generated export files.

The object key layout is the access-control mechanism here — ownership is
encoded in the path rather than in a database table — so the tests are mostly
about the key, and about the fact that one user's export id cannot be used to
reach another user's file.

    poetry run pytest tests/test_export_store.py
"""

import pytest

from services.export_store import (
    ExportNotFound,
    build_object_key,
    parse_object_key,
)

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"
EXPORT = "22222222-2222-2222-2222-222222222222"


def test_the_key_namespaces_by_owner_then_export():
    key = build_object_key(USER, EXPORT, "Report.docx")

    assert key == f"exports/{USER}/{EXPORT}/Report.docx"


def test_the_key_round_trips():
    key = build_object_key(USER, EXPORT, "Report.docx")

    parsed = parse_object_key(key)

    assert parsed["user_id"] == USER
    assert parsed["export_id"] == EXPORT
    assert parsed["filename"] == "Report.docx"


def test_a_filename_cannot_escape_its_own_prefix():
    """A generated filename comes from the model, so it is untrusted input. A
    traversal in it must not be able to point the key at another user."""
    key = build_object_key(USER, EXPORT, "../../../etc/passwd")

    assert key.startswith(f"exports/{USER}/{EXPORT}/")
    assert ".." not in key


def test_a_filename_with_a_path_separator_is_flattened():
    key = build_object_key(USER, EXPORT, "sub/dir/Report.docx")

    assert key == f"exports/{USER}/{EXPORT}/Report.docx"


def test_a_blank_filename_still_produces_a_usable_key():
    key = build_object_key(USER, EXPORT, "")

    assert key.startswith(f"exports/{USER}/{EXPORT}/")
    assert not key.endswith("/")


def test_requires_an_owner():
    with pytest.raises(ValueError):
        build_object_key("", EXPORT, "Report.docx")


def test_requires_an_export_id():
    with pytest.raises(ValueError):
        build_object_key(USER, "", "Report.docx")


# ── Against a real MinIO ──────────────────────────────────────────────────

@pytest.fixture
def store():
    from services import export_store
    try:
        export_store.ensure_bucket()
    except Exception as e:                     # pragma: no cover
        pytest.skip(f"MinIO unreachable: {e}")
    return export_store


def test_a_saved_export_can_be_read_back(store):
    payload = b"PK\x03\x04 pretend docx bytes"

    export_id = store.save_export(
        user_id=USER, filename="Garbage.docx", data=payload,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    got = store.load_export(user_id=USER, export_id=export_id)

    assert got["data"] == payload
    assert got["filename"] == "Garbage.docx"


def test_another_user_cannot_read_it_even_with_the_export_id(store):
    """Ownership lives in the key, so a different user resolves to a different
    path and simply finds nothing."""
    export_id = store.save_export(
        user_id=USER, filename="Private.xlsx", data=b"secret", mime_type="x"
    )

    with pytest.raises(ExportNotFound):
        store.load_export(user_id=OTHER, export_id=export_id)


def test_an_unknown_export_id_is_reported_not_guessed(store):
    with pytest.raises(ExportNotFound):
        store.load_export(user_id=USER, export_id="00000000-0000-0000-0000-000000000000")


def test_the_mime_type_survives_the_round_trip(store):
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    export_id = store.save_export(
        user_id=USER, filename="Data.xlsx", data=b"xlsx", mime_type=mime
    )

    assert store.load_export(user_id=USER, export_id=export_id)["mime_type"] == mime
