"""
eval/precommit_checks.py
─────────────────────────
Regression checks for the evidence-gate routing change, covering the paths the
routing matrix does not: session-history integrity, the general-knowledge
fallback, blended-mode honesty, mixed conversations, degenerate inputs, the
latency cost of the gate, and audit-log wiring.

Run inside the api container:
    docker compose -f docker-compose.yml -f docker-compose.server.yml \
        exec api python -m eval.precommit_checks

These are behavioural checks against a live stack and a real LLM, so a couple of
them assert on model output. Where that is unavoidable the check prints what it
saw and asks for a human read rather than silently passing.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx

from config.settings import get_settings

BASE = os.getenv("SPARKLINE_API_URL", "http://localhost:8000")
USER = os.getenv("SPARKLINE_CHECK_USER", "sidharth.vijayan")

GREEN, RED, YELLOW, BLUE, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[0m"

_results: list[tuple[str, bool | None]] = []


def section(title: str) -> None:
    print(f"\n{BLUE}{'─' * 78}\n  {title}\n{'─' * 78}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")
    _results.append((msg, True))


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")
    _results.append((msg, False))


def review(msg: str) -> None:
    """Needs a human read — cannot be asserted mechanically."""
    print(f"  {YELLOW}[READ]{RESET} {msg}")
    _results.append((msg, None))


def login() -> str:
    """
    Get a session using the service token rather than a user password.

    Deliberately the same route the Open WebUI pipeline uses, so this suite
    exercises the path real traffic takes. It also means these checks keep
    working when someone changes their own password, and that no credential
    has to live in this file.
    """
    token = get_settings().service_token
    if not token:
        raise SystemExit(
            "SERVICE_TOKEN is not set. These checks authenticate the same way the "
            "chat pipeline does; set it in .env and restart the api container."
        )

    r = httpx.post(
        f"{BASE}/auth/service-token",
        headers={"X-Service-Token": token},
        json={"username": USER},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def ask(token: str, query: str, session_id: str | None = None) -> dict:
    payload: dict = {"messages": [{"role": "user", "content": query}]}
    if session_id:
        payload["session_id"] = session_id
    r = httpx.post(
        f"{BASE}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    body["_answer"] = body["choices"][0]["message"]["content"]
    return body


# ── 1. Session history integrity ──────────────────────────────────────
# The router now writes history for gated answers instead of the agent, so a
# turn could plausibly be written twice or not at all. Either would corrupt
# every following turn's context.
async def check_history(token: str) -> None:
    section("1. Session history integrity")
    from services.redis_service import get_history

    session = f"precommit-hist-{uuid.uuid4().hex[:8]}"
    turns = [
        ("which agents sit behind the orchestrator", "documents"),
        ("what is 2 + 2", "general"),
        ("what does the memory manager track", "documents"),
    ]
    for query, _ in turns:
        ask(token, query, session)

    history = await get_history(session, max_turns=20)
    roles = [m["role"] for m in history]
    expected = ["user", "assistant"] * len(turns)

    if roles == expected:
        ok(f"history has exactly {len(turns)} user/assistant pairs, correctly alternating")
    else:
        fail(f"history roles are {roles}, expected {expected}")

    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    if user_msgs == [q for q, _ in turns]:
        ok("each user turn stored once, in order, with the user's own wording")
    else:
        fail(f"user turns stored as {user_msgs}")

    if any(not m["content"].strip() for m in history):
        fail("history contains an empty message")
    else:
        ok("no empty messages written to history")


# ── 2. General-knowledge fallback ─────────────────────────────────────
# The gate can still let through a query the documents do not cover. When that
# happens the user must get an answer, not a refusal.
def check_fallback(token: str) -> None:
    section("2. General-knowledge fallback (refusal must never reach the user)")

    # Phrased to score inside the document band (the corpus is about system
    # architecture) while asking something the corpus cannot answer.
    probes = [
        "what is the leave policy",
        "how many vacation days do employees get",
        "what is the notice period for resignation",
    ]
    seen_fallback = False
    for query in probes:
        result = ask(token, query)
        agent = result.get("agent_type")
        answer = result["_answer"]
        score = result.get("top_rerank_score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
        print(f"    {query!r} → {agent} (score {score_s})")
        print(f"      {answer[:160].strip()}")

        if agent == "general_fallback":
            seen_fallback = True

        lowered = answer.lower()
        if "couldn't find" in lowered or "could not find" in lowered:
            fail(f"user-visible refusal survived for {query!r}")
        else:
            ok(f"no dead-end refusal for {query!r}")

    if seen_fallback:
        ok("general_fallback path exercised at least once")
    else:
        review("fallback never triggered — either the gate routed these to general "
               "directly, or the LLM answered from context. Both are acceptable; "
               "confirm the answers above are useful.")


# ── 2b. Typo tolerance ────────────────────────────────────────────────
# The whole point of the feature, checked through the API rather than against
# the normalizer in isolation: a misspelled question about a document we hold
# must be answered from that document, and a correctly spelled general question
# must not be dragged into the documents by a spurious "correction".
def check_typos(token: str) -> None:
    section("2b. Typo tolerance (misspelled document questions still reach the documents)")

    misspelled = [
        "which agnets sit behnd the orchestratr",
        "who owsn the documnet Q&A layr",
        "what is stroed in MinlO",
    ]
    for query in misspelled:
        result = ask(token, query)
        agent = result.get("agent_type")
        score = result.get("top_rerank_score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
        print(f"    {query!r} → {agent} (score {score_s})")

        if agent in ("document_rag", "document_rag_blended"):
            ok(f"misspelled question routed to documents: {query!r}")
        else:
            fail(f"misspelled question fell out of the documents ({agent}): {query!r}")

    # The other half of the guarantee. A correctly spelled general question must
    # come back untouched; correcting ordinary words toward a small technical
    # corpus once turned "tell me a joke" into "well me a joke".
    for query in ("tell me a joke", "how many days are in a leap year"):
        result = ask(token, query)
        agent = result.get("agent_type")
        print(f"    {query!r} → {agent}")
        if agent in ("general", "general_fallback"):
            ok(f"general question left alone: {query!r}")
        else:
            fail(f"general question pulled into the documents ({agent}): {query!r}")

    # A badly mangled word must not produce a confident invented definition.
    # Correcting retrieval but leaving the prompt with the user's raw word made
    # the model refuse, and the fallback then defined a word that does not exist.
    result = ask(token, "what is diprisiation")
    answer = result["_answer"]
    print(f"    'what is diprisiation' → {result.get('agent_type')}")
    print(f"      {answer[:160].strip()}")
    if "depreciat" in answer.lower():
        ok("heavily misspelled word was read as the word it resembles")
    else:
        review("check the answer above reads 'diprisiation' as 'depreciation' rather "
               "than inventing a meaning for it (needs a document mentioning "
               "depreciation to be ingested)")


# ── 3. Blended-mode honesty ───────────────────────────────────────────
def check_blended(token: str) -> None:
    section("3. Blended mode flags general knowledge")

    result = ask(token, "what is the responsibility split for RBAC")
    agent = result.get("agent_type")
    if agent == "document_rag_blended":
        ok(f"query landed in the blended band as expected (score "
           f"{result.get('top_rerank_score'):.2f})")
    else:
        review(f"expected document_rag_blended, got {agent} — the band boundary may have "
               f"shifted; not a failure, but re-check the thresholds")
    print(f"      {result['_answer'][:240].strip()}")
    review("blended answer above: confirm anything NOT from the documents is marked as such")


# ── 4. Mixed conversation coherence ───────────────────────────────────
def check_mixed(token: str) -> None:
    section("4. Mixed conversation (documents → general → document follow-up)")

    session = f"precommit-mixed-{uuid.uuid4().hex[:8]}"
    script = [
        ("which MCP tools does the enterprise agent pick between", "documents"),
        ("by the way what is 2 + 2", "general"),
        ("and why were those four chosen?", "documents"),
    ]
    for query, expected in script:
        result = ask(token, query, session)
        agent = result.get("agent_type", "")
        is_doc = agent.startswith("document_rag")
        matched = is_doc if expected == "documents" else not is_doc
        score = result.get("top_rerank_score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "—"
        print(f"    {query!r} → {agent} (score {score_s})")
        print(f"      {result['_answer'][:160].strip()}")
        if matched:
            ok(f"routed to {expected} as expected")
        else:
            fail(f"expected {expected}, got {agent}")


# ── 5. Degenerate input ───────────────────────────────────────────────
def check_degenerate(token: str) -> None:
    section("5. Degenerate input")

    cases = {
        "empty string": "",
        "whitespace only": "   ",
        "single punctuation": "?",
        "very long query": "what is " + ("the architecture " * 200) + "?",
        "non-english": "¿cuál es la política de licencias?",
    }
    for label, query in cases.items():
        try:
            result = ask(token, query)
            answer = result["_answer"]
            if answer.strip():
                ok(f"{label}: answered by {result.get('agent_type')} without crashing")
            else:
                fail(f"{label}: empty answer returned")
        except httpx.HTTPStatusError as e:
            # A 4xx on empty input is a legitimate design choice, not a crash.
            if e.response.status_code < 500:
                ok(f"{label}: rejected with HTTP {e.response.status_code} (clean rejection)")
            else:
                fail(f"{label}: server error HTTP {e.response.status_code}")
        except Exception as e:  # noqa: BLE001
            fail(f"{label}: raised {type(e).__name__}: {e}")


# ── 6. Latency cost of the gate ───────────────────────────────────────
# A general question that falls through to the gate pays for one retrieval that
# is then discarded. Worth knowing the size of that tax.
def check_latency(token: str) -> None:
    section("6. Latency")

    samples = {
        "small talk (rules only, no retrieval)": "hi",
        "general via gate (retrieval discarded)": "what is the capital of France",
        "document answer (retrieval used)": "which agents sit behind the orchestrator",
    }
    for label, query in samples.items():
        start = time.perf_counter()
        result = ask(token, query)
        elapsed = time.perf_counter() - start
        print(f"    {elapsed:6.2f}s  {label}  → {result.get('agent_type')}")
    review("latency above: the middle row is the gate's cost on general questions")


# ── 7. Audit log ──────────────────────────────────────────────────────
async def check_audit(token: str) -> None:
    section("7. Audit log records the new agent types")
    from sqlalchemy import text

    from services.postgres_service import AsyncSessionLocal

    marker = f"audit probe {uuid.uuid4().hex[:6]}"
    ask(token, marker)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("select query_text, agent_type from audit_log "
                 "order by created_at desc limit 5")
        )).all()

    print(f"    last 5 audit rows: {[(q[:32], a) for q, a in rows]}")
    if any(marker in (q or "") for q, _ in rows):
        ok("query written to audit_logs")
    else:
        fail("probe query missing from audit_logs")

    if any(a in ("general", "general_fallback", "document_rag", "document_rag_blended")
           for _, a in rows):
        ok("agent_type recorded with a recognised routing value")
    else:
        fail(f"no recognised agent_type in recent rows: {[a for _, a in rows]}")


def main() -> None:
    token = login()

    asyncio.run(check_history(token))
    check_fallback(token)
    check_typos(token)
    check_blended(token)
    check_mixed(token)
    check_degenerate(token)
    check_latency(token)
    asyncio.run(check_audit(token))

    passed = sum(1 for _, r in _results if r is True)
    failed = sum(1 for _, r in _results if r is False)
    reads = sum(1 for _, r in _results if r is None)

    print(f"\n{BLUE}{'═' * 78}{RESET}")
    print(f"  {GREEN}{passed} passed{RESET}, {RED}{failed} failed{RESET}, "
          f"{YELLOW}{reads} need a human read{RESET}")
    print(f"{BLUE}{'═' * 78}{RESET}")
    if failed:
        print(f"  {RED}Do not commit until the failures above are understood.{RESET}")


if __name__ == "__main__":
    main()
