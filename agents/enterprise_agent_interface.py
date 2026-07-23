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
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class UserContext:
    """User context passed to all enterprise agents."""
    user_id: str
    username: str
    department: Optional[str]
    designation: Optional[str]
    session_id: str


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
    def can_handle(self, query: str) -> bool:
        """
        Return True if this adapter can handle the given query.
        Used by the enterprise router to select the right adapter.
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Stub implementations — Dhruv replaces these with real MCP-backed adapters
# ─────────────────────────────────────────────────────────────────────────────

class CRMAgentStub(EnterpriseAgentInterface):
    @property
    def agent_name(self) -> str:
        return "CRM Agent"

    async def handle(self, query: str, user_context: UserContext) -> AgentResponse:
        return AgentResponse(
            answer="[STUB] CRM adapter not yet implemented. Dhruv's workstream.",
            agent_type="enterprise_crm",
            intent="crm_query",
            error="not_implemented",
        )

    def can_handle(self, query: str) -> bool:
        crm_keywords = ["customer", "client", "lead", "deal", "pipeline", "crm"]
        return any(k in query.lower() for k in crm_keywords)


class HRMSAgentStub(EnterpriseAgentInterface):
    @property
    def agent_name(self) -> str:
        return "HRMS Agent"

    async def handle(self, query: str, user_context: UserContext) -> AgentResponse:
        return AgentResponse(
            answer="[STUB] HRMS adapter not yet implemented. Dhruv's workstream.",
            agent_type="enterprise_hrms",
            intent="hrms_query",
            error="not_implemented",
        )

    def can_handle(self, query: str) -> bool:
        hrms_keywords = ["leave", "salary", "employee", "payroll", "attendance", "hrms"]
        return any(k in query.lower() for k in hrms_keywords)


class ERPAgentStub(EnterpriseAgentInterface):
    @property
    def agent_name(self) -> str:
        return "ERP Agent"

    async def handle(self, query: str, user_context: UserContext) -> AgentResponse:
        return AgentResponse(
            answer="[STUB] ERP adapter not yet implemented. Dhruv's workstream.",
            agent_type="enterprise_erp",
            intent="erp_query",
            error="not_implemented",
        )

    def can_handle(self, query: str) -> bool:
        erp_keywords = ["inventory", "purchase order", "invoice", "erp", "stock"]
        return any(k in query.lower() for k in erp_keywords)


class FieldCircleAgentStub(EnterpriseAgentInterface):
    @property
    def agent_name(self) -> str:
        return "Field Circle Agent"

    async def handle(self, query: str, user_context: UserContext) -> AgentResponse:
        return AgentResponse(
            answer="[STUB] Field Circle adapter not yet implemented. Dhruv's workstream.",
            agent_type="enterprise_fieldcircle",
            intent="fieldcircle_query",
            error="not_implemented",
        )

    def can_handle(self, query: str) -> bool:
        fc_keywords = ["site", "field", "project status", "attendance site", "field circle"]
        return any(k in query.lower() for k in fc_keywords)
