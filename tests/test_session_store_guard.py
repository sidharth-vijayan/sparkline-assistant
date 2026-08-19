"""
tests/test_session_store_guard.py
──────────────────────────────────
The integration tests run against the production Qdrant through the tunnel,
isolated only by collection name. These assert that the destructive operations
cannot be pointed at the document corpus, so that isolation does not rest on
whoever writes the next test getting the name right.

    poetry run pytest tests/test_session_store_guard.py
"""

import pytest

from config.settings import get_settings
from services.session_store import SessionDocumentStore

settings = get_settings()


def test_a_store_cannot_be_built_on_the_corpus_collection():
    with pytest.raises(ValueError, match="corpus"):
        SessionDocumentStore(collection_name=settings.qdrant_collection_name)


def test_the_guard_names_the_collection_it_refused():
    with pytest.raises(ValueError) as excinfo:
        SessionDocumentStore(collection_name=settings.qdrant_collection_name)

    assert settings.qdrant_collection_name in str(excinfo.value)


def test_the_default_session_collection_is_still_allowed():
    store = SessionDocumentStore()

    assert store.collection_name == settings.qdrant_session_collection_name


def test_a_test_collection_is_allowed():
    store = SessionDocumentStore(collection_name="sparkline_session_docs_pytest")

    assert store.collection_name == "sparkline_session_docs_pytest"
