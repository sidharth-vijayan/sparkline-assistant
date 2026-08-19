"""
agents/tool_executor.py
────────────────────────
Sandboxed tool-calling loop for the Document RAG agent.

Manages the execution of tool calls returned by the LLM, dispatching
to chart_tool.py or export_tool.py as appropriate.

The LLM calls tools by name in its response; this executor matches
the name and routes to the correct handler.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from access_control.intent_classifier import QueryIntent
from tools.chart_tool import CHART_TOOL_DEFINITION, execute_chart_generation
from tools.export_tool import (
    EXPORT_EXCEL_TOOL_DEFINITION,
    EXPORT_WORD_TOOL_DEFINITION,
    export_to_excel,
    export_to_word,
)

logger = structlog.get_logger(__name__)


class ToolExecutor:
    """Dispatches LLM tool calls to registered tool handlers."""

    def get_tool_definitions(self, intent: QueryIntent) -> list[dict]:
        """Return the tool definitions appropriate for the given intent."""
        if intent == QueryIntent.CHART_REQUEST:
            return [CHART_TOOL_DEFINITION]
        elif intent == QueryIntent.EXPORT_REQUEST:
            return [EXPORT_WORD_TOOL_DEFINITION, EXPORT_EXCEL_TOOL_DEFINITION]
        else:
            # Provide all tools for general document QA (LLM decides if needed)
            return [
                CHART_TOOL_DEFINITION,
                EXPORT_WORD_TOOL_DEFINITION,
                EXPORT_EXCEL_TOOL_DEFINITION,
            ]

    async def execute_tool_calls(
        self,
        tool_calls: list[dict],
        context_chunks: list[dict],
        citations: list[dict] | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a list of tool calls from the LLM and return their results.

        Args:
            tool_calls: OpenAI-format tool_calls from the LLM response
            context_chunks: Retrieved chunk payloads (passed to chart tool for data)
            citations: Citation dicts (passed to export tool for references)

        Returns:
            List of tool result dicts, each with:
              - tool_call_id: matches the LLM's tool call ID
              - tool_name: name of the tool that was called
              - result_summary: short string for the follow-up LLM message
              - output: tool-specific output (chart bytes, export bytes, etc.)
        """
        results: list[dict] = []

        for call in tool_calls:
            call_id = call.get("id", str(uuid.uuid4()))
            function = call.get("function", {})
            tool_name = function.get("name", "")
            raw_args = function.get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                logger.error("tool_executor.invalid_args", tool=tool_name, raw=raw_args)
                results.append({
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "result_summary": f"Error: could not parse tool arguments for {tool_name}",
                    "output": None,
                    "success": False,
                })
                continue

            logger.info("tool_executor.calling", tool=tool_name, call_id=call_id)

            if tool_name == "generate_chart":
                result = execute_chart_generation(
                    code=args.get("code", ""),
                    chart_description=args.get("chart_description", "Chart"),
                    data_context=context_chunks,
                )
                results.append({
                    "tool_call_id": call_id,
                    "tool_name": "generate_chart",
                    "result_summary": (
                        f"Chart generated: {result['description']}"
                        if result["success"]
                        else f"Chart generation failed: {result.get('error')}"
                    ),
                    "output": {
                        "chart_base64": result.get("chart_base64"),
                        "chart_path": result.get("chart_path"),
                        "description": result.get("description"),
                    },
                    "success": result["success"],
                })

            elif tool_name == "export_to_word":
                try:
                    docx_bytes = export_to_word(
                        title=args.get("title", "Sparkline Report"),
                        sections=args.get("sections", []),
                        citations=citations,
                    )
                    filename = f"{args.get('title', 'report').replace(' ', '_')}.docx"
                    mime = ("application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document")
                    delivery = self._deliver(docx_bytes, filename, mime, user_id)
                    results.append({
                        "tool_call_id": call_id,
                        "tool_name": "export_to_word",
                        "result_summary": self._summarise(args.get("title"), delivery),
                        "output": delivery,
                        "success": True,
                    })
                except Exception as e:
                    logger.error("tool_executor.word_export_failed", error=str(e))
                    results.append({
                        "tool_call_id": call_id,
                        "tool_name": "export_to_word",
                        "result_summary": f"Word export failed: {e}",
                        "output": None,
                        "success": False,
                    })

            elif tool_name == "export_to_excel":
                try:
                    xlsx_bytes = export_to_excel(
                        sheet_name=args.get("sheet_name", "Data"),
                        headers=args.get("headers", []),
                        rows=args.get("rows", []),
                    )
                    filename = f"{args.get('sheet_name', 'data').replace(' ', '_')}.xlsx"
                    mime = ("application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet")
                    delivery = self._deliver(xlsx_bytes, filename, mime, user_id)
                    results.append({
                        "tool_call_id": call_id,
                        "tool_name": "export_to_excel",
                        "result_summary": self._summarise(args.get("sheet_name"), delivery),
                        "output": delivery,
                        "success": True,
                    })
                except Exception as e:
                    logger.error("tool_executor.excel_export_failed", error=str(e))
                    results.append({
                        "tool_call_id": call_id,
                        "tool_name": "export_to_excel",
                        "result_summary": f"Excel export failed: {e}",
                        "output": None,
                        "success": False,
                    })
            else:
                logger.warning("tool_executor.unknown_tool", tool=tool_name)
                results.append({
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "result_summary": f"Unknown tool: {tool_name}",
                    "output": None,
                    "success": False,
                })

        return results

    # ── Delivery ──────────────────────────────────────────────────────────

    @staticmethod
    def _deliver(
        data: bytes, filename: str, mime_type: str, user_id: str | None
    ) -> dict:
        """
        Persist a generated file and describe how to fetch it.

        Base64 is still returned so existing API callers keep working, but the
        download link is the part that matters: without somewhere to fetch the
        file from, the bytes were previously built and discarded while the model
        announced success.

        A failure to store is reported rather than raised. The file was
        generated correctly, and losing the link is better than losing the
        answer as well.
        """
        import base64

        out = {
            "file_base64": base64.b64encode(data).decode("utf-8"),
            "filename": filename,
            "mime_type": mime_type,
            "bytes": len(data),
            "export_id": None,
            "download_url": None,
        }
        if not user_id:
            logger.warning("tool_executor.no_owner_for_export", filename=filename)
            return out

        try:
            from config.settings import get_settings
            from gateway.middleware.download_token import create_download_token
            from services.export_store import save_export

            export_id = save_export(
                user_id=user_id, filename=filename, data=data, mime_type=mime_type
            )
            token = create_download_token(user_id=user_id, export_id=export_id)
            base = get_settings().public_api_base_url.rstrip("/")
            out["export_id"] = export_id
            out["download_url"] = f"{base}/exports/{export_id}?token={token}"
        except Exception as e:
            logger.error(
                "tool_executor.export_delivery_failed",
                filename=filename,
                error=str(e),
            )
        return out

    @staticmethod
    def _summarise(title: str | None, delivery: dict) -> str:
        """
        What the model is told about its own tool call.

        It must not be able to claim a successful download that did not happen,
        so the wording follows what was actually stored.
        """
        name = title or delivery.get("filename", "file")
        if delivery.get("download_url"):
            return (
                f"'{name}' was created and saved. A download link has been "
                f"given to the user, so do not offer to send the file another way."
            )
        return (
            f"'{name}' was generated but could not be saved for download. "
            f"Tell the user the export failed and to try again."
        )
