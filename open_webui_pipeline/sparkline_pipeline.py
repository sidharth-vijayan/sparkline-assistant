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
        # Shared secret this pipeline presents to ask the API for a session on
        # behalf of the person chatting. Open WebUI has already authenticated
        # them by this point. No user password is stored here, which is what
        # allows users to change their own passwords without breaking the chat.
        service_token: str = os.getenv("SPARKLINE_SERVICE_TOKEN", "")
        request_timeout_seconds: int = 300
        show_citations: bool = True
        show_agent_type: bool = True
        max_sources_shown: int = 3

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

        # Open WebUI issues its own housekeeping requests against the selected
        # model — conversation titles, tags, follow-up suggestions. They must not
        # reach the orchestrator: each one costs a full PDP + retrieval + rerank
        # + GPU pass, lands in the Redis conversation history as though the user
        # had typed a 2KB prompt, and the answer surfaces in the chat (a "hi"
        # once came back as {"title": "Greeting"}). Answer them here instead.
        task = self._detect_task(body, query)
        if task:
            yield self._handle_task(task, messages)
            return

        # Always fetch fresh token or session for user
        state = self._get_auth(user_id, username, user_email)
        if not state.get("token"):
            yield (
                f"❌ Could not start a Sparkline session for '{user_email or username}'.\n\n"
                "This usually means there is no Sparkline account for that email "
                "address, or the pipeline's service token does not match the API. "
                "Ask an administrator to check."
            )
            return

        # Call orchestrator with auto-retry on 401
        try:
            result = self._call_orchestrator(
                query=query,
                user_id=user_id,
                username=username,
                email=user_email,
                session_id=state["session_id"],
                all_messages=messages,
            )
        except Exception as e:
            yield f"❌ Error communicating with Sparkline Backend: {e}"
            return

        answer = self._extract_answer(result)
        citations = result.get("citations", [])
        agent_type = result.get("agent_type", "")

        if not answer:
            yield (
                "⚠️ The backend returned no answer text"
                f"{' (sources were still retrieved — see below)' if citations else ''}.\n"
            )

        yield self._format_answer(answer, citations, agent_type)

    @staticmethod
    def _detect_task(body: dict, query: str) -> Optional[str]:
        """
        Identify an Open WebUI internal task request.

        Recent versions label these in metadata; older ones don't, so the prompt
        text is checked as well — every task prompt is built from the same
        "### Task:" template, and none of it is anything a user would type.
        """
        metadata = body.get("metadata") or {}
        task = metadata.get("task")
        if task:
            return str(task)

        head = query.lstrip()[:400].lower()
        if not head.startswith("### task:"):
            return None
        if "tag" in head:
            return "tags_generation"
        if "title" in head:
            return "title_generation"
        if "quer" in head:
            return "query_generation"
        return "unknown_task"

    @staticmethod
    def _handle_task(task: str, messages: list[dict]) -> str:
        """
        Answer an Open WebUI task locally, in the JSON shape it expects.

        Titles are derived from the user's first message rather than generated,
        which costs no GPU time and — on a shared box where the LLM is the
        bottleneck — is the difference between a chat that responds instantly
        and one that stalls twice per message.
        """
        if "tag" in task:
            return '{"tags": ["general"]}'

        if "quer" in task:
            return '{"queries": []}'

        # Title: first thing the user actually asked, trimmed to a sane length.
        first_user = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"),
            "",
        )
        title = " ".join(first_user.split())[:48].strip() or "New Chat"
        if len(" ".join(first_user.split())) > 48:
            title = title.rsplit(" ", 1)[0] + "…"
        title = title.replace('"', "'").replace("\\", "")
        return '{"title": "%s"}' % title

    @staticmethod
    def _extract_answer(result: dict) -> str:
        """
        Pull the answer text out of the backend response.

        The gateway speaks the OpenAI chat-completions shape, so the text lives at
        choices[0].message.content. A bare "answer" key is also accepted so this
        keeps working if the backend is ever simplified.
        """
        choices = result.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content")
            if content:
                return content
        return result.get("answer", "") or ""

    def _get_auth(self, user_id: str, username: str, email: str) -> dict:
        """Fetch token, generating/refreshing if missing."""
        if user_id not in self._user_state:
            self._user_state[user_id] = {"token": None, "session_id": self._new_session_id()}

        state = self._user_state[user_id]
        if not state.get("token"):
            state["token"] = self._authenticate(username, email)

        return state

    @staticmethod
    def _identity(username: str, email: str) -> dict:
        """
        Decide how to name this user to the API.

        The email address is authoritative when Open WebUI supplies one: it is
        exact and stable, whereas the display name is free text a user or an
        admin can edit. Deriving the account from a display name means "Suraj P"
        resolves but "Suraj Pansare" silently does not, which surfaces as a
        login failure nobody can explain.

        Only one identifier is sent, never both — a display name that happens to
        munge into another person's username must not be able to match.
        """
        if email and "@" in email:
            return {"email": email.strip().lower()}
        return {"username": (username or "").strip().lower().replace(" ", ".")}

    def _authenticate(self, username: str, email: str) -> Optional[str]:
        """
        Ask the API for a session for this user, using the service token.

        Replaces logging in as the user with a shared password. The pipeline
        holds one secret of its own and never handles user credentials.
        """
        if not self.valves.service_token:
            print(
                "[SparklinePipe] No service_token configured — set the "
                "service_token valve (or SPARKLINE_SERVICE_TOKEN) to the value "
                "of SERVICE_TOKEN in the API's .env"
            )
            return None

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{self.valves.sparkline_api_url}/auth/service-token",
                    headers={"X-Service-Token": self.valves.service_token},
                    json=self._identity(username, email),
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token")
                print(
                    f"[SparklinePipe] Auth returned HTTP {resp.status_code}: {resp.text}"
                )
        except Exception as e:
            print(f"[SparklinePipe] Auth exception: {e}")
        return None

    def _call_orchestrator(
        self,
        query: str,
        user_id: str,
        username: str,
        email: str,
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
                new_token = self._authenticate(username, email)
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
            shown = self._dedupe_citations(citations, self.valves.max_sources_shown)
            label = "Source" if len(shown) == 1 else "Sources"
            parts.append(f"\n\n---\n**📚 {label}:**")
            for i, citation in enumerate(shown, start=1):
                doc = citation.get("document_name", "Unknown")
                page = citation.get("page_number")
                uploaded = str(citation.get("version_uploaded_at", ""))[:10]
                page_str = f"p.{page}" if page else ""
                prefix = f"  {i}. " if len(shown) > 1 else "  "
                parts.append(f"{prefix}**{doc}** {page_str} *(uploaded {uploaded})*")

        if self.valves.show_agent_type and agent_type:
            parts.append(f"\n*{self._agent_label(agent_type)}*")

        return "\n".join(parts)

    @staticmethod
    def _agent_label(agent_type: str) -> str:
        """
        Human-readable footer describing where the answer came from.

        Routing is automatic and invisible, so the user never chose a mode — which
        makes it our job to say whether an answer is grounded in Sparkline
        documents or is the model's own general knowledge. Silently mixing the two
        is the failure mode to avoid in an enterprise setting.
        """
        return {
            "document_rag": "📄 Answered from Sparkline documents",
            "document_rag_blended": "📄 Partly from Sparkline documents — "
                                    "general knowledge where the documents were silent",
            "general": "🌐 Answered from general knowledge — no Sparkline documents used",
            "general_fallback": "🌐 Not covered by the Sparkline documents — "
                                "answered from general knowledge",
        }.get(agent_type, f"🤖 Agent: {agent_type}")

    @staticmethod
    def _dedupe_citations(citations: list[dict], max_shown: int) -> list[dict]:
        """
        Collapse citations down to one entry per source document.

        The backend returns one citation per retrieved chunk, ranked best-first, so
        several entries commonly point at the same file — which renders as the same
        filename listed three or four times. Keep only each document's highest-ranked
        chunk, capped at max_shown. A single-document answer therefore shows exactly
        one source.

        Note this is display-only: the full per-chunk citation list still comes back
        from the API and is what the audit log records, so traceability is unchanged.
        """
        seen: set[str] = set()
        deduped: list[dict] = []
        for citation in citations:
            name = citation.get("document_name", "Unknown")
            if name in seen:
                continue
            seen.add(name)
            deduped.append(citation)
            if len(deduped) >= max_shown:
                break
        return deduped

    @staticmethod
    def _new_session_id() -> str:
        import uuid
        return str(uuid.uuid4())
