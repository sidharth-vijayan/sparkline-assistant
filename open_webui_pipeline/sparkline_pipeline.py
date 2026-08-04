"""
open_webui_pipeline/sparkline_pipeline.py
──────────────────────────────────────────
Open WebUI Pipe Function with Live Status Updates.

This script creates a virtual model in Open WebUI that routes queries
through the Sparkline FastAPI orchestrator. Uses a generator to emit
instant progress status to avoid HTTP timeouts on CPU inference.
"""

from __future__ import annotations

import os
from typing import Optional, Union, Generator, Iterator

import httpx
from pydantic import BaseModel


class Pipe:
    """Sparkline RAG Pipeline as an Open WebUI Pipe."""

    class Valves(BaseModel):
        """User-configurable pipeline settings (shown in Open WebUI UI)."""
        sparkline_api_url: str = os.getenv("SPARKLINE_API_URL", "http://host.docker.internal:8000")
        request_timeout_seconds: int = 300
        show_citations: bool = True
        show_agent_type: bool = True

    def __init__(self):
        self.type = "pipe"
        self.name = "Sparkline RAG"
        self.id = "sparkline"
        self.valves = self.Valves()
        # Per-user state: {user_id: {"token": str, "session_id": str}}
        self._user_state: dict[str, dict] = {}

    def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
    ) -> Generator[str, None, None]:
        """
        Handle the chat completion request with generator streaming to prevent timeouts.
        """
        user_id = (__user__ or {}).get("id", "anonymous")
        username = (__user__ or {}).get("name", "anonymous")
        user_email = (__user__ or {}).get("email", "")

        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            yield "No query provided."
            return

        query = user_messages[-1].get("content", "")

        # Always fetch fresh token or session for user
        state = self._get_auth(user_id, username)
        if not state.get("token"):
            yield f"❌ Authentication failed for user '{username}'. Could not log into Sparkline Backend."
            return

        # Call orchestrator with auto-retry on 401
        try:
            result = self._call_orchestrator(
                query=query,
                user_id=user_id,
                username=username,
                session_id=state["session_id"],
                all_messages=messages,
            )
        except Exception as e:
            yield f"❌ Error communicating with Sparkline Backend: {e}"
            return

        answer = result.get("answer", "")
        citations = result.get("citations", [])
        agent_type = result.get("agent_type", "")

        yield self._format_answer(answer, citations, agent_type)

    def _get_auth(self, user_id: str, username: str) -> dict:
        """Fetch token, generating/refreshing if missing."""
        if user_id not in self._user_state:
            self._user_state[user_id] = {"token": None, "session_id": self._new_session_id()}

        state = self._user_state[user_id]
        if not state.get("token"):
            state["token"] = self._login(username)

        return state

    def _login(self, username: str) -> Optional[str]:
        api_username = username.lower().replace(" ", ".")
        password = "Sparkline@2025"
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{self.valves.sparkline_api_url}/auth/login",
                    json={"username": api_username, "password": password},
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token")
                else:
                    print(f"[SparklinePipe] Login returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[SparklinePipe] Login exception: {e}")
        return None

    def _call_orchestrator(
        self,
        query: str,
        user_id: str,
        username: str,
        session_id: str,
        all_messages: list[dict],
    ) -> dict:
        token = self._user_state[user_id].get("token")
        
        with httpx.Client(timeout=self.valves.request_timeout_seconds) as client:
            resp = client.post(
                f"{self.valves.sparkline_api_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messages": all_messages,
                    "session_id": session_id,
                },
            )
            
            # If 401 Unauthorized, refresh token once and retry
            if resp.status_code == 401:
                print("[SparklinePipe] Token expired or invalid (401). Refreshing token...")
                new_token = self._login(username)
                if new_token:
                    self._user_state[user_id]["token"] = new_token
                    resp = client.post(
                        f"{self.valves.sparkline_api_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {new_token}"},
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
        import uuid
        return str(uuid.uuid4())
