"""
agents/enterprise_agent_interface.py
──────────────────────────────────────
Interface contract for Dhruv's enterprise adapter agents.

This stub defines the clean interface that enterprise adapters
(CRM, HRMS, ERP, Field Circle via MCP) must implement to plug into
the shared orchestrator without touching any shared infrastructure code.

Dhruv owns the MCP implementations. This file defines only the contract.
The orchestrator calls handle() polymorphically — it doesn't care which
concrete adapter is behind it.

If Dhruv's adapters need to reference Sparkline policy documents,
the PDP/PEP can be exposed as a service — the architecture supports
this without changes to the shared infrastructure.

──────────────────────────────────────────────────────────────────────────────
How a question is routed when ERP and the documents both cover the subject
──────────────────────────────────────────────────────────────────────────────
Much of what is in ERP is also described in the documents — the same customers,
the same invoicing, the same processes. So "which source should answer this?"
cannot be settled by looking at what the question is *about*. Both sources are
about the same things. That is not a gap to be closed by better keywords; it is
a property of the data, and it gets worse as more systems are connected.

What does separate them is what the question asks *for*:

    documents  →  what is written down.  Policies, processes, definitions.
                  Stable. Answers "what are we supposed to do".
    ERP        →  what is currently true. Balances, quantities, statuses.
                  Live. Answers "what is actually the case right now".

    "What is our credit policy for dealers?"      → document
    "What is dealer X's outstanding balance?"     → ERP

Both are "credit". Only the second needs a system behind it.

Four rules follow, and together they are the whole routing design:

 1. The adapter decides, not the orchestrator. The orchestrator cannot see
    inside ERP — it does not know which invoices or vendors exist — so any
    judgement it makes about ownership is a guess about data it has never seen.
    `assess()` asks the adapter, which does know, and returns a comparable
    score rather than a yes/no.

 2. A named record wins. If the query contains an identifier the adapter can
    resolve, it belongs to that adapter. Documents describe categories of
    thing; systems hold the individual instances.

 3. Precedence is declared per entity, once, not argued per query. Where both
    sources genuinely cover an entity, live system data wins for current
    values and documents win for rules and process. Configuration, not
    inference.

 4. Every answer names its source. Routing is invisible to the user, so the
    answer has to say whether a figure came from ERP or from a named document.
    A wrong route then shows up immediately as a visible wrong source, instead
    of as a confident number nobody can trace.

When the adapter's score and the document retrieval score are close, prefer
asking the user which they meant over silently picking one. During a pilot that
is not a weakness: it is how we find out what people actually meant, which is
the data needed to set these thresholds properly.

Status: no adapter is connected yet, so nothing here is wired into the router.
Every stub below declines every question. This file is the contract to build
against, not a live routing path.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


@dataclass
class UserContext:
    """User context passed to all enterprise agents."""
    user_id: str
    username: str
    department: Optional[str]
    designation: Optional[str]
    session_id: str


class QuestionKind(str, Enum):
    """
    What a question is asking for, which is what decides its source.

    The same subject matter appears in the documents and in the business
    systems — a written credit policy and a live outstanding balance are both
    "credit". Routing on subject matter is therefore ambiguous by construction
    and always will be. Routing on what is being asked for is not.
    """

    # "What is dealer X's outstanding balance?" — a live value, in a system.
    CURRENT_STATE = "current_state"
    # "How many purchase orders did we raise in July?" — transactions, in a system.
    HISTORICAL_RECORD = "historical_record"
    # "What is our credit policy for dealers?" — what is written down, in a document.
    POLICY_OR_DEFINITION = "policy_or_definition"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Coverage:
    """
    An adapter's own assessment of whether a question is its to answer.

    This exists because the orchestrator cannot see inside an enterprise system.
    It does not know which customers, sites or invoice numbers exist, so any
    guess it makes about ownership is a guess about data it has never seen. The
    adapter does know. So the adapter decides, and the orchestrator compares.

    `score` is a confidence, not a match count, and must be comparable across
    adapters — 0.0 means "definitely not mine", 1.0 means "certainly mine, I
    have located the exact record". Anything in between should reflect how much
    of the question the adapter can actually resolve.
    """

    score: float
    reason: str
    question_kind: QuestionKind = QuestionKind.UNKNOWN

    # Concrete records the adapter recognised in the query — an invoice number,
    # a customer code, a site name. A named record is the strongest possible
    # signal that a question belongs to a system rather than to a document,
    # because documents describe categories while systems hold instances.
    entities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Coverage.score must be within 0.0-1.0, got {self.score}")


# An adapter claiming less than this is treated as declining the question.
COVERAGE_THRESHOLD = 0.5


@dataclass
class AgentResponse:
    """Standard response from any agent (RAG or enterprise)."""
    answer: str
    agent_type: str
    intent: str
    citations: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "agent_type": self.agent_type,
            "intent": self.intent,
            "citations": self.citations,
            "tool_outputs": self.tool_outputs,
            "metadata": self.metadata,
            "error": self.error,
        }


class EnterpriseAgentInterface(ABC):
    """
    Abstract base class that all of Dhruv's enterprise adapter agents must implement.

    Each adapter (CRM, HRMS, ERP, Field Circle) is a concrete subclass.
    The orchestrator dispatches to handle() without knowing which adapter is running.
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Human-readable name for this adapter (e.g., 'CRM Agent')."""
        ...

    @property
    @abstractmethod
    def owned_entities(self) -> tuple[str, ...]:
        """
        The record types this adapter is the source of truth for, declared once
        (e.g. ("invoice", "purchase_order", "stock_item")).

        Two adapters that both claim an entity type are a configuration error to
        be resolved between their owners, not something the orchestrator should
        be silently arbitrating at query time.
        """
        ...

    @abstractmethod
    async def handle(
        self,
        query: str,
        user_context: UserContext,
    ) -> AgentResponse:
        """
        Handle an enterprise query.

        Args:
            query: User's natural language query
            user_context: Authenticated user's attributes and session

        Returns:
            AgentResponse with the answer and any relevant metadata
        """
        ...

    @abstractmethod
    async def assess(self, query: str, user_context: UserContext) -> Coverage:
        """
        Say how well this adapter covers the query, without answering it.

        Replaces the previous boolean `can_handle`. A boolean could not express
        the case this system actually has to resolve: ERP content and document
        content describe the same subjects, so a keyword like "invoice" or
        "credit" is evidence for both and decides nothing. A score can be
        compared against the document retrieval score; a boolean cannot.

        Must be cheap and must not have side effects — it is called before the
        orchestrator has decided anything. Looking up whether an identifier
        exists is fine; running the actual report is not.

        Return Coverage(score=0.0, ...) when the query is not yours. Never raise:
        an adapter that errors here is treated as declining, so that a failing
        integration degrades to "documents only" instead of breaking the chat.
        """
        ...

    async def can_handle(self, query: str, user_context: UserContext) -> bool:
        """Convenience wrapper over assess() for call sites that want a boolean."""
        return (await self.assess(query, user_context)).score >= COVERAGE_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Stub implementations — Dhruv replaces these with real MCP-backed adapters
# ─────────────────────────────────────────────────────────────────────────────

class _NotImplementedAdapter(EnterpriseAgentInterface):
    """
    Shared body for the not-yet-built adapters.

    Every one of them declines every question, deliberately. Until an adapter is
    backed by a real system, declining is the only honest answer it can give:
    a plausible-looking figure with no data behind it is worse than saying the
    assistant does not cover that yet.
    """

    _agent_type = "enterprise"
    _entities: tuple[str, ...] = ()

    @property
    def owned_entities(self) -> tuple[str, ...]:
        return self._entities

    async def handle(self, query: str, user_context: UserContext) -> AgentResponse:
        return AgentResponse(
            answer=f"[STUB] {self.agent_name} not yet implemented. Dhruv's workstream.",
            agent_type=self._agent_type,
            intent=f"{self._agent_type}_query",
            error="not_implemented",
        )

    async def assess(self, query: str, user_context: UserContext) -> Coverage:
        return Coverage(
            score=0.0,
            reason=f"{self.agent_name} has no backing system connected yet",
            question_kind=QuestionKind.UNKNOWN,
        )


class CRMAgentStub(_NotImplementedAdapter):
    _agent_type = "enterprise_crm"
    _entities = ("customer", "lead", "deal", "opportunity")

    @property
    def agent_name(self) -> str:
        return "CRM Agent"


class HRMSAgentStub(_NotImplementedAdapter):
    _agent_type = "enterprise_hrms"
    _entities = ("employee", "leave_request", "payslip", "attendance_record")

    @property
    def agent_name(self) -> str:
        return "HRMS Agent"


# Document-number prefixes as they actually appear in Sparkline's ERP
# (confirmed by Dhruv, 2026-08-13). No separators anywhere: the prefix runs
# straight into a financial year and a serial, e.g. SL3012627000486.
# Longer prefixes are listed first — the alternation is ordered, and "SR" would
# otherwise swallow the "SRAMD" prefix and mis-read the code.
_ERP_DOCUMENT_RE = re.compile(
    r"\b(?:SODCR|SODSE|EXFOC|SRAMD|HSS|SL|SR|PO|PA|SO|SA)\d{8,}\b"
)

# Vendor and customer codes: one letter, four digits (F0045, S0226, A0001).
# NOT unique on their own — 3,183 of 5,584 master rows share a code, so the real
# key is (party status, party code). Detecting one is a strong hint that this is
# an ERP question, but never enough to identify a single party.
_ERP_PARTY_RE = re.compile(r"\b[FSA]\d{4}\b")

# GL account: 13 characters with a party code embedded (11016101F0045).
_ERP_GL_RE = re.compile(r"\b\d{8}[FSA]\d{4}\b")


class ERPAgentStub(_NotImplementedAdapter):
    """
    ERP is the adapter actually being built, so this stub carries the worked
    example of what `assess` is supposed to do. It is still inert — `handle`
    declines — but the assessment shows the shape a real implementation takes,
    against the real identifier formats and the real coverage limits.
    """

    _agent_type = "enterprise_erp"

    # What this ERP is the source of truth for (Dhruv, 2026-08-13).
    _entities = (
        "purchase_invoice", "grn", "sales_invoice", "credit_note",
        "sales_order", "delivery_schedule", "purchase_order",
        "vendor_master", "customer_master", "item_master",
        "gl_posting", "payment", "receipt", "gst_tax_line",
    )

    # Declared gaps — subjects the ERP integration cannot answer at all. These
    # are refused explicitly rather than guessed at, and they are refused rather
    # than quietly handed to the documents, because a document describing how
    # stock is managed is not an answer to "how much stock do we have".
    _unsupported = (
        "stock on hand", "stock in hand", "closing stock", "inventory level",
        "outstanding", "ageing", "aging", "receivable", "payable balance",
        "bom", "bill of materials", "fixed asset", "depreciation schedule",
        "qc result", "quality check", "tds", "proforma", "price list",
    )

    @property
    def agent_name(self) -> str:
        return "ERP Agent"

    async def assess(self, query: str, user_context: UserContext) -> Coverage:
        """
        Illustrative only — replace the pattern matching with real lookups.

        The ordering is the part worth copying. A recognised identifier outranks
        a recognised phrasing, which outranks a recognised topic, because that is
        the order of how much the adapter actually knows about the question. A
        topic word alone ("invoice", "vendor") is the weakest signal there is: it
        is exactly as consistent with a question about the written invoicing
        procedure, which belongs to the documents.
        """
        lowered = query.lower()

        # Declared gaps first. Claiming a question and then failing is worse than
        # never claiming it, and worse still is answering it approximately.
        for gap in self._unsupported:
            if gap in lowered:
                return Coverage(
                    score=0.0,
                    reason=f"'{gap}' is outside what the ERP integration exposes",
                    question_kind=QuestionKind.UNKNOWN,
                )

        # Strongest: the query names a record. Documents describe categories of
        # thing; only a system holds the individual instances.
        identifiers = tuple(
            _ERP_GL_RE.findall(query)
            or _ERP_DOCUMENT_RE.findall(query)
            or _ERP_PARTY_RE.findall(query)
        )
        if identifiers:
            return Coverage(
                score=1.0,
                reason="query names a specific ERP record",
                question_kind=QuestionKind.HISTORICAL_RECORD,
                entities=identifiers,
            )

        # Next: phrasing that only makes sense against transaction data.
        # Note these are HISTORICAL_RECORD, not CURRENT_STATE — this ERP records
        # movements, not balances, so it answers "what was invoiced" and cannot
        # answer "what is owed".
        record_phrases = (
            "how many", "how much", "total", "list", "raised", "issued",
            "invoiced", "ordered", "received", "last month", "this year",
            "in june", "in july", "between",
        )
        topic_words = (
            "invoice", "credit note", "purchase order", "sales order", "grn",
            "vendor", "customer", "item", "payment", "receipt", "gst",
        )
        has_topic = any(w in lowered for w in topic_words)

        if has_topic and any(p in lowered for p in record_phrases):
            return Coverage(
                score=0.8,
                reason="asks for a figure from ERP transaction records",
                question_kind=QuestionKind.HISTORICAL_RECORD,
            )

        # Weakest: the topic is ours but the question reads like it is about how
        # something is done, which is what the documents are for. Score below
        # the threshold so the documents win unless they retrieve nothing.
        if has_topic:
            return Coverage(
                score=0.3,
                reason="ERP topic, but phrased as a policy or process question",
                question_kind=QuestionKind.POLICY_OR_DEFINITION,
            )

        return Coverage(score=0.0, reason="no ERP entity or phrasing recognised")


class FieldCircleAgentStub(_NotImplementedAdapter):
    _agent_type = "enterprise_fieldcircle"
    _entities = ("site", "site_visit", "field_task")

    @property
    def agent_name(self) -> str:
        return "Field Circle Agent"
