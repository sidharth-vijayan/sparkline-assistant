"""
tests/test_unit_components.py
──────────────────────────────
Unit tests for the Sparkline RAG core components.
Tests logic and verify that imports and syntax are correct.
Does not require external services (Postgres, Redis, MinIO, Qdrant).

Usage:
    poetry run pytest tests/test_unit_components.py
    # or
    .venv\\Scripts\\pytest tests/test_unit_components.py
"""

# ── Mock tiktoken module to bypass corporate firewall blocking ────────────
import sys
from unittest.mock import MagicMock

mock_tiktoken = MagicMock()
mock_encoding = MagicMock()
# Simple mock: count tokens as words to avoid downloading files from internet
mock_encoding.encode = lambda text: [1] * len(text.split())
mock_encoding.decode = lambda tokens: " ".join(["mock_word"] * len(tokens))
mock_tiktoken.get_encoding.return_value = mock_encoding
sys.modules['tiktoken'] = mock_tiktoken
# ─────────────────────────────────────────────────────────────────────────

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from config.settings import Settings
from access_control.intent_classifier import QueryIntent, classify_intent
from access_control.pdp import PDPDecision, UserAttributes, evaluate as pdp_evaluate
from access_control.pep import build_qdrant_filter
from ingestion.chunker import chunk_text, TextChunk
from retrieval.citation_builder import Citation, build_citations, build_context_block
from router.route_decision import (
    Route,
    build_retrieval_query,
    is_followup,
    is_refusal,
    pre_route,
)


def test_settings_validation():
    """Verify that settings can be loaded and compute URLs correctly."""
    settings = Settings(
        postgres_user="test_user",
        postgres_password="test_password",
        postgres_host="test_host",
        postgres_port=5432,
        postgres_db="test_db",
        app_secret_key="a" * 32,  # Must be >= 32 chars
        # Pinned explicitly: unset fields fall through to the ambient environment,
        # and in the api container QDRANT_HOST is 'qdrant', not 'localhost'.
        qdrant_host="localhost",
        qdrant_port=6333,
    )
    assert "test_user:test_password@test_host:5432/test_db" in settings.database_url
    assert settings.qdrant_url == "http://localhost:6333"


@pytest.mark.parametrize("query,expected_intent", [
    ("create a bar chart of the sales data", QueryIntent.CHART_REQUEST),
    ("export this report to word", QueryIntent.EXPORT_REQUEST),
    ("find document about safety protocols", QueryIntent.DOCUMENT_SEARCH),
    ("according to the policy, how much sick leave do I get", QueryIntent.DOCUMENT_QA),
    # Unmarked questions fall back to DOCUMENT_QA as the PDP resource category.
    # That is no longer a routing decision — the evidence gate in QueryRouter
    # decides whether the answer actually comes from documents.
    ("what is 2 + 2?", QueryIntent.DOCUMENT_QA),
])
def test_intent_classifier(query, expected_intent):
    """Verify that user queries are correctly classified into intents."""
    assert classify_intent(query) == expected_intent


@pytest.mark.parametrize("query,expected_route", [
    # Small talk — no document could answer these, so retrieval is skipped.
    ("hi", Route.GENERAL),
    ("hello there", Route.GENERAL),
    ("thanks!", Route.GENERAL),
    ("who are you", Route.GENERAL),
    ("what can you do", Route.GENERAL),
    # Explicit document language — honour it without a relevance check.
    ("according to the safety policy, who signs off audits", Route.DOCUMENTS),
    ("what does the contract say about penalties", Route.DOCUMENTS),
    ("summarise the uploaded file", Route.DOCUMENTS),
    ("what is in project work split.docx", Route.DOCUMENTS),
    # A greeting in front of a document question is still a document question.
    ("hi, according to the policy how much leave do I get", Route.DOCUMENTS),
    # Everything else defers to retrieval scores — the common case.
    ("what is 2 + 2", Route.UNDECIDED),
    ("what is the standard warranty period", Route.UNDECIDED),
    ("which agents sit behind the orchestrator", Route.UNDECIDED),
    # "hi" must not fire inside an ordinary word.
    ("which frontend is used as the chat client", Route.UNDECIDED),
])
def test_pre_route(query, expected_route):
    """Verify the pre-retrieval routing rules."""
    assert pre_route(query) == expected_route


@pytest.mark.parametrize("answer,expected", [
    ("I couldn't find this in the available documents.", True),
    ("I couldn't find any relevant documents for your query.", True),
    ("I could not find that information in the provided sources.", True),
    ("The leave policy allows 12 days of annual leave.", False),
    ("", False),
    # A grounded answer that merely notes a gap must NOT be discarded — the
    # fallback would replace real cited content with general knowledge.
    (
        "The Q3 report gives revenue of 4.2 crore and expenses of 3.1 crore. "
        "It does not contain a segment-wise breakdown in the provided document, "
        "but the summary on page 4 states that the equipment division accounted "
        "for the majority of the growth, with the remainder split across the "
        "services and rental lines as detailed in the appendix table.",
        False,
    ),
])
def test_refusal_detection(answer, expected):
    """The safety net must recognise a dead-end RAG answer."""
    assert is_refusal(answer) == expected


@pytest.mark.parametrize("query,expected", [
    # Genuinely dependent on an earlier turn.
    ("what about last year?", True),
    ("why?", True),
    ("and the second one", True),
    ("explain that further", True),
    ("and why were those four chosen?", True),
    ("tell me more", True),
    ("how about the other one?", True),
    # Self-contained — must NOT be glued to the previous document question.
    # Every one of these was misclassified by the earlier length-based rule and
    # reached users as a document refusal.
    ("what is 2 + 2", False),
    ("explain what depreciation means", False),
    ("which agents sit behind the orchestrator", False),
    ("what is the leave policy", False),
    ("write a python function", False),
    # "that" as a relative pronoun in a longer question is not anaphora.
    ("what is the policy that applies to unpaid leave for contractors", False),
    ("what are the standard safety requirements for operating heavy equipment", False),
])
def test_followup_detection(query, expected):
    """
    Follow-ups are detected by grammatical dependency, not by length.

    Regression guard: treating every short query as a follow-up sent
    "what is 2 + 2" into the document pipeline attached to an unrelated
    question, where it scored 4.34, was refused, and took ~6s to answer.
    """
    assert is_followup(query) == expected


def test_build_retrieval_query_expands_followups():
    """A follow-up is expanded with the anchor question — for retrieval only."""
    anchor = "what does the leave policy say"

    assert build_retrieval_query("what about sick leave?", anchor) == (
        "what does the leave policy say what about sick leave?"
    )
    # A self-contained question is left alone.
    long_query = "which MCP tools does the enterprise agent pick between and why"
    assert build_retrieval_query(long_query, anchor) == long_query
    # No anchor to draw on — nothing to expand with.
    assert build_retrieval_query("why?", None) == "why?"
    assert build_retrieval_query("why?", "") == "why?"


def test_pdp_evaluation():
    """Test Policy Decision Point rules under different conditions."""
    # Active vs Inactive User
    inactive_user = UserAttributes(
        user_id=str(uuid.uuid4()),
        username="inactive.bob",
        department="HR",
        designation="Manager",
        default_role="user",
        is_active=False,
        is_admin=False,
        is_file_admin=False
    )
    res = pdp_evaluate(inactive_user, QueryIntent.DOCUMENT_QA)
    assert res.decision == PDPDecision.DENY
    assert "inactive" in res.reason.lower()

    # Admin user executing admin tasks
    admin_user = UserAttributes(
        user_id=str(uuid.uuid4()),
        username="admin.alice",
        department="IT",
        designation="Director",
        default_role="admin",
        is_active=True,
        is_admin=True,
        is_file_admin=False
    )
    res = pdp_evaluate(admin_user, QueryIntent.ADMIN)
    assert res.decision == PDPDecision.ALLOW
    assert res.full_access is True

    # Non-admin user trying to execute admin tasks
    regular_user = UserAttributes(
        user_id=str(uuid.uuid4()),
        username="regular.joe",
        department="Sales",
        designation="Executive",
        default_role="user",
        is_active=True,
        is_admin=False,
        is_file_admin=False
    )
    res = pdp_evaluate(regular_user, QueryIntent.ADMIN)
    assert res.decision == PDPDecision.DENY

    # Pilot user with no department/designation (temporary pilot role stand-in)
    pilot_user = UserAttributes(
        user_id=str(uuid.uuid4()),
        username="siddharth.doshi",
        department=None,
        designation=None,
        default_role="pilot_user",
        is_active=True,
        is_admin=False,
        is_file_admin=False
    )
    res = pdp_evaluate(pilot_user, QueryIntent.DOCUMENT_QA)
    assert res.decision == PDPDecision.ALLOW
    assert res.full_access is True

    # Scoped user with department
    scoped_user = UserAttributes(
        user_id=str(uuid.uuid4()),
        username="scoped.sam",
        department="HR",
        designation="Officer",
        default_role="user",
        is_active=True,
        is_admin=False,
        is_file_admin=False
    )
    res = pdp_evaluate(scoped_user, QueryIntent.DOCUMENT_QA)
    assert res.decision == PDPDecision.ALLOW
    assert res.permitted_departments == ["HR"]
    assert res.permitted_designations == ["Officer"]
    assert res.full_access is False


def test_pep_filter_builder():
    """Test that the Policy Enforcement Point builds Qdrant filters correctly."""
    # Denied result
    deny_result = MagicMock()
    deny_result.decision = PDPDecision.DENY
    assert build_qdrant_filter(deny_result) is None

    # Full access result
    full_access_result = MagicMock()
    full_access_result.decision = PDPDecision.ALLOW
    full_access_result.full_access = True
    q_filter = build_qdrant_filter(full_access_result)
    assert q_filter is not None
    # Must have active version condition
    assert q_filter.must[0].key == "is_active_version"
    assert q_filter.must[0].match.value is True

    # Scoped access result
    scoped_result = MagicMock()
    scoped_result.decision = PDPDecision.ALLOW
    scoped_result.full_access = False
    scoped_result.allow_public_only = False
    scoped_result.permitted_departments = ["HR"]
    scoped_result.permitted_designations = ["Manager"]
    
    q_filter = build_qdrant_filter(scoped_result)
    assert q_filter is not None
    assert len(q_filter.must) == 1
    assert q_filter.must[0].key == "is_active_version"
    # Should check conditions: is_public, allowed_departments, allowed_designations
    assert len(q_filter.min_should.conditions) == 3
    assert q_filter.min_should.min_count == 1


def test_chunker():
    """Test text chunking with token constraints."""
    text = "This is a simple sentence that we want to chunk. It has some words."
    chunks = chunk_text(text, page_number=1, chunk_size=10, chunk_overlap=2)
    assert len(chunks) > 0
    assert all(c.page_number == 1 for c in chunks)
    assert all(c.token_count > 0 for c in chunks)


def test_citations():
    """Test citation parsing and context block assembly."""
    chunks = [
        {
            "document_name": "Safety_Guidelines.pdf",
            "page_number": 3,
            "uploaded_at": "2026-07-21T10:00:00Z",
            "text": "Always wear personal protective equipment when on site.",
            "rerank_score": 0.85
        },
        {
            "document_name": "HR_Leave_Policy.docx",
            "page_number": None,
            "uploaded_at": "2026-07-20T12:00:00Z",
            "text": "Sick leave requires a medical certificate if longer than three days.",
            "rerank_score": 0.72
        }
    ]

    citations = build_citations(chunks)
    assert len(citations) == 2
    assert citations[0].document_name == "Safety_Guidelines.pdf"
    assert citations[0].page_number == 3
    assert "protective equipment" in citations[0].chunk_text_preview
    assert citations[1].page_number is None

    context_block = build_context_block(chunks, citations)
    assert "[SOURCE 1] Document: Safety_Guidelines.pdf | Page: 3" in context_block
    assert "[SOURCE 2] Document: HR_Leave_Policy.docx | Page: N/A" in context_block
