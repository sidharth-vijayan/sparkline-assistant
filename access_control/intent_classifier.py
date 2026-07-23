"""
access_control/intent_classifier.py
──────────────────────────────────────
Classify the intent of an incoming query for the PDP.

The PDP uses intent to determine what resource category a query maps to,
which informs the access control decision alongside user attributes and
target resource metadata.

Intent categories:
  - DOCUMENT_QA:     General document question and answer
  - DOCUMENT_SEARCH: Searching for specific documents
  - CHART_REQUEST:   User wants a chart or graph generated
  - EXPORT_REQUEST:  User wants a Word/Excel export
  - GENERAL_QUERY:   General Q&A not requiring document access
  - ADMIN:           Administrative operations (file upload, user management)

This is a lightweight keyword/pattern classifier — no LLM call needed
for intent classification (keeps it fast and deterministic).
"""

from __future__ import annotations

import re
from enum import Enum


class QueryIntent(str, Enum):
    DOCUMENT_QA = "document_qa"
    DOCUMENT_SEARCH = "document_search"
    CHART_REQUEST = "chart_request"
    EXPORT_REQUEST = "export_request"
    GENERAL_QUERY = "general_query"
    ADMIN = "admin"


# Keyword patterns per intent (checked in priority order)
_INTENT_PATTERNS: list[tuple[QueryIntent, list[str]]] = [
    (
        QueryIntent.ADMIN,
        ["upload", "ingest", "add document", "manage user", "set permission"],
    ),
    (
        QueryIntent.CHART_REQUEST,
        [
            "chart", "graph", "plot", "visualize", "bar chart", "pie chart",
            "line graph", "histogram", "trend", "show me a chart",
        ],
    ),
    (
        QueryIntent.EXPORT_REQUEST,
        [
            "export", "download", "save as word", "save as excel",
            "generate report", "create report", "word document", ".docx", ".xlsx",
        ],
    ),
    (
        QueryIntent.DOCUMENT_SEARCH,
        [
            "find document", "search for", "list documents", "which documents",
            "what files", "show documents about",
        ],
    ),
    (
        QueryIntent.DOCUMENT_QA,
        [
            "according to", "based on", "in the document", "policy says",
            "what does the", "summarize", "explain the", "what is the",
            "how does", "when was", "where is", "who is responsible",
        ],
    ),
]


def classify_intent(query: str) -> QueryIntent:
    """
    Classify the intent of a user query.

    Uses pattern matching — fast, deterministic, no LLM call.
    Falls back to DOCUMENT_QA (most common case) if no pattern matches,
    since this system's primary use case is document Q&A.

    Args:
        query: Raw user query string

    Returns:
        QueryIntent enum value
    """
    query_lower = query.lower()

    for intent, patterns in _INTENT_PATTERNS:
        for pattern in patterns:
            if re.search(re.escape(pattern), query_lower):
                return intent

    # Default: assume document Q&A
    return QueryIntent.DOCUMENT_QA
