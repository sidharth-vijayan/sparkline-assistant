"""
open_webui_pipeline/sparkline_pipeline.py
──────────────────────────────────────────
Open WebUI Function/Pipeline using the `inlet` method.

This pipeline intercepts all user messages in Open WebUI and routes
them through the Sparkline FastAPI orchestrator instead of directly
to Ollama. The orchestrator returns RAG-augmented answers with citations.

Installation:
  1. In Open WebUI, go to Workspace → Pipelines
  2. Click "+" → Import from file → select this file
  3. Set the SPARKLINE_API_URL environment variable to your FastAPI URL
  4. Assign this pipeline to the users or model you want to use it for

The pipeline stores the JWT token per-user (obtained on first message)
and sends the session_id so conversation history is maintained.

References:
  https://docs.openwebui.com/features/plugin/pipelines/
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Generator, Iterator, Optional, Union

import httpx
from pydantic import BaseModel


class Pipeline:
    """Sparkline RAG Pipeline for Open WebUI."""

    class Valves(BaseModel):
        """User-configurable pipeline settings (shown in Open WebUI UI)."""
        sparkline_api_url: str = os.getenv("SPARKLINE_API_URL", "http://localhost:8000")
        request_timeout_seconds: int = 120
        show_citations: bool = True
        show_agent_type: bool = True

    def __init__(self):
        self.name = "Sparkline AI — RAG Pipeline"
        self.valves = self.Valves()
        # Per-user state: {user_id: {"token": str, "session_id": str}}
        self._user_state: dict[str, dict] = {}

    async def on_startup(self):
        print(f"[SparklinePipeline] Started. API: {self.valves.sparkline_api_url}")

    async def on_shutdown(self):
        print("[SparklinePipeline] Shutting down.")

    def inlet(
        self,
        body: dict,
        user: Optional[dict] = None,
    ) -> dict:
        """
        Intercept the user message before it reaches the LLM.

        Forwards the message to the Sparkline FastAPI orchestrator,
        which handles RAG retrieval, access control, and LLM generation.
        The response replaces what would have gone to Ollama directly.
        """
        # Extract user info from Open WebUI's user dict
        user_id = (user or {}).get("id", "anonymous")
        username = (user or {}).get("name", "anonymous")
        user_email = (user or {}).get("email", "")

        # Get the latest user message
        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return body

        query = user_messages[-1].get("content", "")

        # Ensure the user has a token + session
        state = self._ensure_auth(user_id, username, user_email)
        if not state.get("token"):
            # Auth failed — pass through to Ollama directly
            return body

        # Call the Sparkline orchestrator
        try:
            result = self._call_orchestrator(
                query=query,
                token=state["token"],
                session_id=state["session_id"],
                all_messages=messages,
            )
        except Exception as e:
            print(f"[SparklinePipeline] Orchestrator call failed: {e}")
            return body

        # Inject the RAG answer back into the message body
        # Replace the last user message with a pre-computed assistant response
        # that Open WebUI will display directly
        answer = result.get("answer", "")
        citations = result.get("citations", [])
        agent_type = result.get("agent_type", "")

        # Format citations for display
        formatted_answer = self._format_answer(answer, citations, agent_type)

        # Replace the messages with a synthetic exchange so Open WebUI
        # displays the RAG answer as if the LLM generated it
        body["messages"] = messages[:-1] + [
            {"role": "user", "content": query},
            {"role": "assistant", "content": formatted_answer},
        ]

        # Store tool outputs (charts/exports) in metadata if present
        tool_outputs = result.get("tool_outputs", [])
        if tool_outputs:
            body["_sparkline_tool_outputs"] = tool_outputs

        return body

    def _ensure_auth(self, user_id: str, username: str, email: str) -> dict:
        """Get or create auth state for a user."""
        if user_id not in self._user_state:
            self._user_state[user_id] = {"token": None, "session_id": self._new_session_id()}

        state = self._user_state[user_id]
        if not state.get("token"):
            # Try to log in with the user's Open WebUI username
            # Default password is the pilot password — change on first login
            token = self._login(username)
            state["token"] = token

        return state

    def _login(self, username: str) -> Optional[str]:
        """Authenticate against Sparkline API and return JWT token."""
        # Normalize username: Open WebUI uses full names, API uses lowercase.dotted
        api_username = username.lower().replace(" ", ".")
        # Default pilot password (should be changed on first login)
        password = "Sparkline@2025"

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.valves.sparkline_api_url}/auth/login",
                    json={"username": api_username, "password": password},
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token")
        except Exception as e:
            print(f"[SparklinePipeline] Login failed for {username}: {e}")
        return None

    def _call_orchestrator(
        self,
        query: str,
        token: str,
        session_id: str,
        all_messages: list[dict],
    ) -> dict:
        """Call the Sparkline /v1/chat/completions endpoint."""
        with httpx.Client(timeout=self.valves.request_timeout_seconds) as client:
            resp = client.post(
                f"{self.valves.sparkline_api_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messages": all_messages,
                    "session_id": session_id,
                },
            )
            resp.raise_for_status()
            return resp.json()

    def _format_answer(
        self,
        answer: str,
        citations: list[dict],
        agent_type: str,
    ) -> str:
        """Format the answer with citations for display in Open WebUI."""
        parts = [answer]

        if citations and self.valves.show_citations:
            parts.append("\n\n---\n**📚 Sources:**")
            for i, citation in enumerate(citations, start=1):
                doc = citation.get("document_name", "Unknown")
                page = citation.get("page_number")
                uploaded = str(citation.get("version_uploaded_at", ""))[:10]
                page_str = f"p.{page}" if page else ""
                parts.append(f"  {i}. **{doc}** {page_str} *(uploaded {uploaded})*")

        if self.valves.show_agent_type and agent_type and agent_type != "general":
            parts.append(f"\n*🤖 Agent: {agent_type}*")

        return "\n".join(parts)

    @staticmethod
    def _new_session_id() -> str:
        """Generate a new session ID."""
        import uuid
        return str(uuid.uuid4())
