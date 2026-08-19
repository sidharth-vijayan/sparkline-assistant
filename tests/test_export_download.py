"""
tests/test_export_download.py
──────────────────────────────
Tests for the export download endpoint.

Exercised through the real app so the auth wiring is covered, not just the
handler. The interesting cases are the refusals: this endpoint serves files by
id, and a link to it gets pasted into a chat window.

    poetry run pytest tests/test_export_download.py
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.middleware.auth import get_current_user_optional
from gateway.middleware.download_token import create_download_token

OWNER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())

PAYLOAD = b"PK\x03\x04 pretend export bytes"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id
        self.username = "tester"


@pytest.fixture
def stored_export():
    from services import export_store
    try:
        export_store.ensure_bucket()
    except Exception as e:                     # pragma: no cover
        pytest.skip(f"MinIO unreachable: {e}")
    return export_store.save_export(
        user_id=OWNER, filename="Quarterly.docx", data=PAYLOAD, mime_type=MIME
    )


@pytest.fixture
def client():
    return TestClient(app)


def as_user(user_id):
    app.dependency_overrides[get_current_user_optional] = lambda: FakeUser(user_id)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ── The link in the chat: token in the query string ───────────────────────

def test_a_valid_link_returns_the_file(client, stored_export):
    token = create_download_token(user_id=OWNER, export_id=stored_export)

    r = client.get(f"/exports/{stored_export}", params={"token": token})

    assert r.status_code == 200
    assert r.content == PAYLOAD


def test_the_response_names_the_file_for_the_browser(client, stored_export):
    token = create_download_token(user_id=OWNER, export_id=stored_export)

    r = client.get(f"/exports/{stored_export}", params={"token": token})

    assert "Quarterly.docx" in r.headers.get("content-disposition", "")


def test_a_link_with_no_token_is_refused(client, stored_export):
    r = client.get(f"/exports/{stored_export}")

    assert r.status_code in (401, 403)


def test_a_token_for_another_export_is_refused(client, stored_export):
    """A leaked link must grant one file, not the store."""
    wrong = create_download_token(user_id=OWNER, export_id=str(uuid.uuid4()))

    r = client.get(f"/exports/{stored_export}", params={"token": wrong})

    assert r.status_code in (401, 403)


def test_a_token_naming_a_different_owner_cannot_fetch_it(client, stored_export):
    """The token is signed, so the owner claim is trustworthy — but it must
    actually be used, not just decoded."""
    imposter = create_download_token(user_id=OTHER, export_id=stored_export)

    r = client.get(f"/exports/{stored_export}", params={"token": imposter})

    assert r.status_code == 404


def test_garbage_token_is_refused(client, stored_export):
    r = client.get(f"/exports/{stored_export}", params={"token": "nonsense"})

    assert r.status_code in (401, 403)


# ── Logged-in API access ──────────────────────────────────────────────────

def test_the_owner_can_download_with_a_normal_session(client, stored_export):
    as_user(OWNER)

    r = client.get(f"/exports/{stored_export}")

    assert r.status_code == 200
    assert r.content == PAYLOAD


def test_another_logged_in_user_cannot_download_it(client, stored_export):
    as_user(OTHER)

    r = client.get(f"/exports/{stored_export}")

    assert r.status_code == 404


def test_an_unknown_export_is_a_404_for_its_own_owner(client):
    as_user(OWNER)

    r = client.get(f"/exports/{uuid.uuid4()}")

    assert r.status_code == 404
