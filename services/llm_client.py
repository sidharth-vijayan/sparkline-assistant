"""
services/llm_client.py
───────────────────────
OpenAI-compatible HTTP client for local LLM serving.

Works with Ollama (dev) and vLLM (prod) — both expose the same
OpenAI-compatible /v1/chat/completions API.

To switch backends: change LLM_BASE_URL + LLM_MODEL_NAME in .env.
This file and all callers remain unchanged.

Supports:
  - Streaming and non-streaming completion
  - Tool/function calling (for the sandboxed executor in Week 5)
  - Configurable timeout and retry via tenacity
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


def _get_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def chat_completion(
    messages: list[dict[str, str]],
    tools: Optional[list[dict]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stream: bool = False,
) -> dict[str, Any]:
    """
    Call the OpenAI-compatible chat completions endpoint (non-streaming).

    Args:
        messages: List of {"role": "...", "content": "..."} dicts
        tools: Optional tool definitions for function calling
        temperature: Override LLM_TEMPERATURE
        max_tokens: Override LLM_MAX_TOKENS
        stream: If True, use streaming (returns generator — use chat_stream instead)

    Returns:
        Full response dict (OpenAI-compatible format)
    """
    payload: dict[str, Any] = {
        "model": settings.llm_model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.llm_temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers=_get_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    logger.debug(
        "llm.completion",
        model=settings.llm_model_name,
        input_tokens=data.get("usage", {}).get("prompt_tokens"),
        output_tokens=data.get("usage", {}).get("completion_tokens"),
    )
    return data


async def chat_stream(
    messages: list[dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream chat completion tokens as they are generated.

    Yields individual text delta strings.
    """
    payload: dict[str, Any] = {
        "model": settings.llm_model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.llm_temperature,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            headers=_get_headers(),
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[len("data: "):]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


def extract_text_response(completion: dict) -> str:
    """Extract the assistant's text from a non-streaming completion response."""
    try:
        return completion["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        return ""


def extract_tool_calls(completion: dict) -> list[dict]:
    """Extract tool call requests from a completion response (for function calling)."""
    try:
        return completion["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError):
        return []
