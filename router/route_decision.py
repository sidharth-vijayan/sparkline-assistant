"""
router/route_decision.py
─────────────────────────
Pre-retrieval routing rules and the evidence-gate helpers used by QueryRouter.

Why this exists as its own module: the routing decision is deliberately kept
separate from QueryIntent. QueryIntent feeds the PDP, so it carries access
control meaning; overloading it with "should this answer come from documents"
would entangle two unrelated concerns.

The routing strategy is *retrieve first, decide on the evidence*:

  1. Rules run first, but only for cases where retrieval is pointless or the
     user has been explicit. Small talk never needs a vector search; "according
     to the policy document" never needs a relevance check.
  2. Everything else is UNDECIDED and falls through to the evidence gate in
     QueryRouter, which reads the cross-encoder rerank score of the best
     retrieved chunk and picks strict-RAG / blended / general from it.

Keyword lists cannot make this decision on their own. "What is the standard
warranty period" is a document question at a company that has ingested its
warranty policy and a general-knowledge question anywhere else. The words are
identical; only the corpus knows. That is why the gate is score-based and the
rules here stay deliberately narrow.
"""

from __future__ import annotations

import re
from enum import Enum


class Route(str, Enum):
    """Where a query should be answered from."""

    GENERAL = "general"        # answer from the LLM's own knowledge, no retrieval
    DOCUMENTS = "documents"    # answer from the corpus, skip the score gate
    UNDECIDED = "undecided"    # let retrieval scores decide


# ── Small talk / identity / meta ──────────────────────────────────────
# Retrieval is pointless for these: there is no document that answers "hi".
# Anchored at the start of the message so "hi" doesn't fire inside "which".
_GENERAL_PATTERNS: list[str] = [
    r"^\s*(hi|hey|hello|yo|hiya)\b",
    r"^\s*good (morning|afternoon|evening|night)\b",
    r"^\s*(thanks|thank you|thx|ty|cheers)\b",
    r"^\s*(bye|goodbye|see ya|see you)\b",
    r"\bwho are you\b",
    r"\bwhat (are|can) you (do|help with|capable of)\b",
    r"\bwhat is your (name|purpose)\b",
    r"\bare you (an? )?(ai|bot|human|robot|llm)\b",
    r"\bhow are you\b",
    r"\b(tell|say) me a joke\b",
]

# ── Explicit document language ────────────────────────────────────────
# The user has named a source. Honour that even if retrieval scores poorly —
# a low score then means "we looked and the documents don't cover it", which is
# a legitimate answer worth giving, rather than a reason to silently switch to
# general knowledge.
_DOCUMENT_PATTERNS: list[str] = [
    r"\bin the (document|doc|file|report|policy|guide|manual|spec)\b",
    r"\baccording to\b",
    r"\bas per the\b",
    r"\bthe (document|policy|report|guide|manual|contract|agreement) (say|state|mention)",
    r"\b(our|the) company('s)?\b",
    r"\bwhat do(es)? (the|our) .{0,30}(document|policy|report|guide|contract)\b",
    r"\b(uploaded|attached|ingested)\b",
    r"\.(pdf|docx?|xlsx?|pptx?)\b",
]

_GENERAL_RE = [re.compile(p, re.IGNORECASE) for p in _GENERAL_PATTERNS]
_DOCUMENT_RE = [re.compile(p, re.IGNORECASE) for p in _DOCUMENT_PATTERNS]

# The refusal the strict RAG prompt is instructed to emit. If this reaches the
# user it means the score gate sent a query to documents that the documents do
# not actually cover — a dead end for the user, so the router retries in general
# mode instead of returning it.
_REFUSAL_RE = re.compile(
    r"\b(could ?n[o']?t|can ?not|can'?t|unable to|do(es)? not) "
    r"(find|locate|see|contain|answer)\b.{0,60}?"
    r"\b(documents?|sources?|contexts?|passages?|"
    r"information provided|provided information)\b",
    re.IGNORECASE,
)

# A refusal is a short dead-end reply. Beyond this length the message is far more
# likely to be a real answer that merely mentions a gap ("the report doesn't give
# a 2024 figure, but section 3 states...") — replacing that with a general-
# knowledge answer would throw away grounded content the user wanted.
_REFUSAL_MAX_CHARS = 320

# Follow-ups that carry no retrievable content of their own. "What about last
# year?" embeds to nothing useful, scores near the floor, and would be misrouted
# to general — losing the thread of a document conversation.
#
# Detection is by grammar, NOT by length. An earlier version treated any query of
# six words or fewer as a follow-up, which swept up self-contained questions:
# "what is 2 + 2" and "explain what depreciation means" were glued onto whatever
# document question came before them, scoring them into the document band and
# producing a refusal the fallback then had to clean up — 14s to answer a
# general-knowledge question. Short does not mean dependent.

# Openers that only make sense as a continuation of something already said.
_FOLLOWUP_STARTERS = (
    "what about", "how about", "and why", "and what", "and how", "and who",
    "and when", "and where", "but why", "but what", "so why", "so what",
    "why not", "what if", "and the", "and those", "and that",
)

# Complete utterances that are pure continuations.
_FOLLOWUP_EXACT = {
    "why", "why?", "why is that", "why is that?", "how", "how?", "how so",
    "how so?", "more", "more?", "tell me more", "go on", "continue",
    "elaborate", "and?", "and", "explain", "really", "really?",
}

# Demonstratives and pronouns that point at something earlier in the
# conversation. Only treated as anaphoric in a short query — in a longer one
# "that" is usually a relative pronoun ("the policy that applies to leave").
_ANAPHORA_RE = re.compile(
    r"\b(those|these|that|this|they|them|it|he|she|his|her|its|their|the same)\b",
    re.IGNORECASE,
)
_ANAPHORA_MAX_WORDS = 8


def pre_route(query: str) -> Route:
    """
    Apply the deterministic rules that run before any retrieval.

    Returns Route.UNDECIDED for the large majority of real queries — that is the
    intended outcome, not a failure. Only clear-cut cases are decided here.
    """
    text = query.strip()
    if not text:
        return Route.GENERAL

    # Document intent wins over small talk: "hi, what does the safety policy say
    # about helmets" is a document question with a greeting stapled to the front.
    for pattern in _DOCUMENT_RE:
        if pattern.search(text):
            return Route.DOCUMENTS

    for pattern in _GENERAL_RE:
        if pattern.search(text):
            return Route.GENERAL

    return Route.UNDECIDED


def is_refusal(answer: str) -> bool:
    """True if a RAG answer is the 'not in the documents' dead end."""
    text = (answer or "").strip()
    if not text or len(text) > _REFUSAL_MAX_CHARS:
        return False
    return bool(_REFUSAL_RE.search(text))


def is_followup(query: str) -> bool:
    """
    True if the query only makes sense as a continuation of an earlier one.

    Used to decide whether to prepend the anchor question to the *retrieval*
    query. Being wrong here is expensive in both directions: a missed follow-up
    loses the thread of a document conversation, and a false positive drags an
    unrelated question into the document band. So this asks whether the query is
    grammatically dependent, not whether it is short.
    """
    text = " ".join(query.strip().lower().split())
    if not text:
        return False

    if text.rstrip("?.! ") in _FOLLOWUP_EXACT or text in _FOLLOWUP_EXACT:
        return True

    if text.startswith(_FOLLOWUP_STARTERS):
        return True

    if len(text.split()) <= _ANAPHORA_MAX_WORDS and _ANAPHORA_RE.search(text):
        return True

    return False


def build_retrieval_query(query: str, anchor: str | None) -> str:
    """
    Expand a follow-up into a self-contained retrieval query.

    Args:
        anchor: The question this follow-up is understood to continue — normally
            the last question that was answered from documents, NOT simply the
            previous turn. Users drop general questions into the middle of a
            document conversation, and anchoring on the previous turn drags that
            detour into the retrieval query, which sinks the score and loses the
            thread.

    Only the retrieval query changes — the LLM still receives the user's own
    words, so the answer never reads as though it responded to a question that
    was never asked.
    """
    if not anchor or not is_followup(query):
        return query
    return f"{anchor} {query}"
