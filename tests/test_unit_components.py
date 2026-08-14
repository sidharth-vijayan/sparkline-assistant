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
# A bare MagicMock has no __spec__, and importlib raises "tiktoken.__spec__ is
# not set" as soon as anything imports a module that imports tiktoken — which
# includes the gateway routes. Give the stub a real spec so those imports work.
from importlib.machinery import ModuleSpec  # noqa: E402
mock_tiktoken.__spec__ = ModuleSpec("tiktoken", loader=None)
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


# ═════════════════════════════════════════════════════════════════════════════
# Query normalizer (typo tolerance)
# ═════════════════════════════════════════════════════════════════════════════

import asyncio

import ingestion.bm25_index as _bm25_module
import retrieval.query_normalizer as _qn
from retrieval.query_normalizer import _skeleton, correct_typos


@pytest.fixture
def corpus():
    """
    Install a synthetic corpus vocabulary.

    Bumps the vocabulary epoch so the normalizer rebuilds its lookup index, which
    is the same path an ingestion takes in production.
    """
    original_vocabulary = _bm25_module._vocabulary
    original_epoch = _bm25_module._vocabulary_epoch
    original_dictionary = _qn._known_words_cache

    # Empty English dictionary by default: it keeps these tests off the reranker
    # model (which would otherwise be loaded just to read its tokenizer) and
    # lets each test exercise the one guard it is about. The dictionary guard
    # has its own test below, which sets this explicitly.
    _qn._known_words_cache = frozenset()

    def _set(words):
        _bm25_module._vocabulary = frozenset(words)
        _bm25_module._vocabulary_epoch += 1

    yield _set

    _bm25_module._vocabulary = original_vocabulary
    _bm25_module._vocabulary_epoch = original_epoch + 1
    _qn._known_words_cache = original_dictionary


def test_typo_correction_uses_corpus_words(corpus):
    corpus({"which", "agents", "sit", "behind", "the", "orchestrator"})
    result = correct_typos("which agnets sit behnd the orchestratr")

    assert result.text == "which agents sit behind the orchestrator"
    assert set(result.as_log_value()) == {
        "agnets→agents", "behnd→behind", "orchestratr→orchestrator"
    }


def test_typo_correction_follows_the_current_corpus(corpus):
    """
    The guarantee that matters when new documents are uploaded: corrections come
    from whatever is ingested now, never from a list baked into the code. The
    same misspelling must be corrected against one corpus and left alone against
    another that does not contain the word.
    """
    corpus({"depreciation", "machinery", "rate"})
    assert "depreciation" in correct_typos("what is the depriciation rate").text

    corpus({"orchestrator", "agents", "rate"})
    unchanged = correct_typos("what is the depriciation rate")
    assert unchanged.text == "what is the depriciation rate"
    assert "depriciation" in unchanged.unresolved


def test_typo_correction_leaves_general_questions_alone(corpus):
    """
    Punctuation and digits must survive untouched. Rebuilding the query by
    joining tokens dropped the "+" from "what is 2 + 2", and the resulting
    "what is 2 2" scored above the routing floor — a general question routed
    into a document set that says nothing about it.
    """
    corpus({"minio", "stored", "what", "documents"})
    assert correct_typos("what is 2 + 2").text == "what is 2 + 2"
    assert correct_typos("hi").text == "hi"


def test_typo_correction_preserves_surrounding_characters(corpus):
    corpus({"minio", "what", "is", "stored"})
    result = correct_typos("what is stored in MinlO?")

    assert result.text == "what is stored in Minio?"
    assert result.text.endswith("?")


def test_typo_correction_skips_tokens_below_minimum_length(corpus):
    corpus({"the", "stock"})
    # "hte" is three characters — too short to correct safely, since almost
    # everything is within one edit of it.
    assert correct_typos("hte stock").text == "hte stock"


def test_typo_correction_rejects_inflections(corpus):
    """
    A candidate differing only by a grammatical ending is not a correction.
    Observed over-correction: "write" became "writes" purely because the corpus
    happened to contain the plural.
    """
    corpus({"writes", "function"})
    assert correct_typos("write a function").text == "write a function"


def test_typo_correction_phonetic_layer(corpus):
    """Sound-alike misspellings too far off for edit distance."""
    corpus({"depreciation", "what"})
    result = correct_typos("what is diprisiation")

    assert result.text == "what is depreciation"
    assert [c.method for c in result.corrections] == ["phonetic"]


def test_typo_correction_protects_ordinary_english_words(corpus):
    """
    A word the documents do not use is not automatically a misspelling. With a
    small corpus most ordinary words are absent, and correcting them turned
    "tell me a joke" into "well me a joke" — two real words one edit apart,
    where the corpus happened to contain only the wrong one.
    """
    corpus({"well", "look", "live"})
    _qn._known_words_cache = frozenset({"tell", "book", "give"})

    assert correct_typos("tell me about the book").text == "tell me about the book"
    assert correct_typos("give me tips").text == "give me tips"

    # A genuine misspelling is still corrected — the guard protects real words,
    # it does not switch correction off.
    corpus({"orchestrator", "well"})
    assert correct_typos("the orchestratr").text == "the orchestrator"


def test_typo_correction_no_corpus_is_a_no_op(corpus):
    corpus(set())
    assert correct_typos("which agnets sit behnd").text == "which agnets sit behnd"


def test_typo_correction_can_be_disabled(corpus, monkeypatch):
    corpus({"agents"})
    monkeypatch.setattr(_qn.settings, "typo_correction_enabled", False)
    assert correct_typos("agnets").text == "agnets"


@pytest.mark.parametrize("a,b", [
    ("depreciation", "diprisiation"),
    ("committee", "comittee"),
    ("photograph", "fotograf"),
])
def test_skeleton_collapses_sound_alike_spellings(a, b):
    assert _skeleton(a) == _skeleton(b)


def test_skeleton_separates_unrelated_words():
    assert _skeleton("invoice") != _skeleton("inventory")


# ═════════════════════════════════════════════════════════════════════════════
# Enterprise adapter coverage contract
# ═════════════════════════════════════════════════════════════════════════════

from agents.enterprise_agent_interface import (
    COVERAGE_THRESHOLD,
    Coverage,
    CRMAgentStub,
    ERPAgentStub,
    HRMSAgentStub,
    QuestionKind,
    UserContext,
)


def _ctx():
    return UserContext(
        user_id="u", username="suraj.p", department=None,
        designation=None, session_id="s",
    )


def test_coverage_score_must_be_a_probability():
    with pytest.raises(ValueError):
        Coverage(score=1.5, reason="out of range")
    with pytest.raises(ValueError):
        Coverage(score=-0.1, reason="out of range")


def test_erp_assessment_ranks_identifier_above_topic():
    """
    The case Dhruv raised: the same topic word appears in a question the ERP
    owns and a question the documents own, so the topic cannot decide it. What
    decides it is whether the question asks for a live value or for what is
    written down.
    """
    erp = ERPAgentStub()

    # Real Sparkline document number — prefix, financial year and serial run
    # together with no separator.
    named = asyncio.run(erp.assess("what is the status of SL3012627000486", _ctx()))
    assert named.score == 1.0
    assert named.entities == ("SL3012627000486",)

    records = asyncio.run(erp.assess("how many purchase orders were raised last month", _ctx()))
    assert records.score >= COVERAGE_THRESHOLD

    written = asyncio.run(erp.assess("what is our invoice approval process", _ctx()))
    assert written.score < COVERAGE_THRESHOLD
    assert written.question_kind is QuestionKind.POLICY_OR_DEFINITION


@pytest.mark.parametrize("code", [
    "SL3012627000486",   # sales invoice
    "SRAMD2627000017",   # sales return amendment — must not be read as "SR"
    "SODCR2425000131",   # sales order
    "PO2032526000003",   # purchase order
    "HSS202627000001",   # high seas sales
    "11016101F0045",     # GL account with an embedded party code
])
def test_erp_recognises_real_identifier_formats(code):
    coverage = asyncio.run(ERPAgentStub().assess(f"show me {code}", _ctx()))
    assert coverage.score == 1.0, code
    assert coverage.entities == (code,), code


def test_erp_declines_its_declared_gaps():
    """
    Subjects the ERP integration cannot answer at all. Claiming one and then
    failing is worse than never claiming it — and answering it approximately,
    from a document that merely describes how stock is managed, is worse again.
    """
    erp = ERPAgentStub()
    for query in (
        "how much stock do we have on hand",
        "what is the outstanding receivable for S0226",
        "show me the bill of materials",
        "what is the TDS deducted",
    ):
        coverage = asyncio.run(erp.assess(query, _ctx()))
        assert coverage.score == 0.0, query


def test_unbuilt_adapters_decline_everything():
    """An adapter with no system behind it must never claim a question."""
    for adapter in (CRMAgentStub(), HRMSAgentStub()):
        coverage = asyncio.run(adapter.assess("how many leaves do I have", _ctx()))
        assert coverage.score == 0.0
        assert not asyncio.run(adapter.can_handle("anything at all", _ctx()))


# ═════════════════════════════════════════════════════════════════════════════
# Document formats
# ═════════════════════════════════════════════════════════════════════════════

from ingestion.parsers.excel_parser import _looks_like_legacy_xls, parse_excel

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # pre-2007 .xls signature


def test_legacy_xls_is_detected_by_content_not_extension():
    assert _looks_like_legacy_xls(_OLE2 + b"\x00" * 64)
    assert not _looks_like_legacy_xls(b"PK\x03\x04" + b"\x00" * 64)  # a real .xlsx


def test_corrupt_legacy_xls_fails_cleanly():
    """
    A .xls that antiword's counterpart xlrd cannot read must raise a ValueError
    naming the file, not leak a library error or crash the request. openpyxl's
    own message for these bytes is "File is not a zip file" — true, and
    meaningless to whoever uploaded it.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_excel(_OLE2 + b"\x00" * 4096, "quarterly_report.xls")

    message = str(excinfo.value)
    assert "quarterly_report.xls" in message
    assert "zip" not in message.lower()


def test_xlsm_is_an_accepted_upload_format():
    """Macro-enabled workbooks are how most real business spreadsheets are saved."""
    from gateway.routes.ingest import ALLOWED_EXTENSIONS
    assert ".xlsm" in ALLOWED_EXTENSIONS
    assert ".xlsx" in ALLOWED_EXTENSIONS
    assert ".docx" in ALLOWED_EXTENSIONS
    assert ".pdf" in ALLOWED_EXTENSIONS


def _chunk(page, index):
    return TextChunk(text=f"page {page} chunk {index}", chunk_index=index,
                     page_number=page, token_count=10, source_block_indices=[index])


def test_small_documents_are_never_capped():
    from ingestion.pipeline import _cap_chunks
    chunks = [_chunk(p, i) for p in range(1, 4) for i in range(10)]
    kept, truncation = _cap_chunks(chunks, budget=1000)
    assert kept == chunks
    assert truncation is None


def test_oversized_documents_keep_every_sheet_represented():
    """
    A 20-sheet workbook truncated by taking the first N chunks would lose the
    last dozen sheets entirely, and a question about December would silently
    return nothing. Truncation is spread so every sheet stays searchable.
    """
    from ingestion.pipeline import _cap_chunks
    chunks = [_chunk(sheet, i) for sheet in range(1, 21) for i in range(500)]
    kept, truncation = _cap_chunks(chunks, budget=1000)

    assert len(kept) == 1000
    assert truncation["chunks_produced"] == 10000
    assert truncation["chunks_indexed"] == 1000
    assert truncation["pages_or_sheets"] == 20

    # Every sheet present, none favoured over another.
    per_sheet = {}
    for c in kept:
        per_sheet[c.page_number] = per_sheet.get(c.page_number, 0) + 1
    assert sorted(per_sheet) == list(range(1, 21))
    assert set(per_sheet.values()) == {50}


def test_truncation_message_tells_the_uploader_what_happened():
    from ingestion.pipeline import _cap_chunks
    chunks = [_chunk(p, i) for p in range(1, 6) for i in range(400)]
    _, truncation = _cap_chunks(chunks, budget=100)
    assert "2,000 sections" in truncation["message"]
    assert "summary sheet" in truncation["message"]
