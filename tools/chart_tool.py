"""
tools/chart_tool.py
────────────────────
Chart generation tool for the LLM tool-calling loop.

The LLM generates Python code using matplotlib/pandas to create charts
from retrieved Excel/tabular data. This module provides:
  1. The tool definition (for the LLM's tools parameter)
  2. The execution handler (calls the sandbox, returns chart PNG bytes)

Supported chart types: bar, line, pie, scatter, histogram
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from tools.sandbox import execute_code

logger = structlog.get_logger(__name__)

CHART_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "generate_chart",
        "description": (
            "Generate a chart or graph from tabular data using matplotlib and pandas. "
            "Use this when the user asks for a visual chart, graph, or plot. "
            "Write Python code using matplotlib.pyplot and pandas to create the chart "
            "and save it as 'chart.png' in the current directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to generate the chart. Must import matplotlib.pyplot as plt "
                        "and pandas as pd. Must save the chart as 'chart.png' using "
                        "plt.savefig('chart.png', bbox_inches='tight', dpi=150). "
                        "Do not call plt.show()."
                    ),
                },
                "chart_description": {
                    "type": "string",
                    "description": "Human-readable description of what the chart shows.",
                },
            },
            "required": ["code", "chart_description"],
        },
    },
}


def execute_chart_generation(
    code: str,
    chart_description: str,
    data_context: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Execute chart generation code in the sandbox.

    Args:
        code: LLM-generated matplotlib/pandas code
        chart_description: Description from the LLM
        data_context: Optional raw chunk payloads to inject as context

    Returns:
        {
            "success": bool,
            "chart_base64": str | None,   # PNG as base64 string
            "chart_path": str | None,     # Absolute path if saved to disk
            "description": str,
            "error": str | None
        }
    """
    output_dir = tempfile.mkdtemp(prefix="sparkline_chart_")

    # Inject data from context chunks as a Python comment for reference
    if data_context:
        data_snippet = "\n".join(
            f"# {c.get('document_name', 'doc')}: {c.get('text', '')[:200]}"
            for c in data_context[:3]
        )
        code = f"# Retrieved context:\n{data_snippet}\n\n{code}"

    result = execute_code(code=code, output_dir=output_dir)

    chart_base64 = None
    chart_path = None

    if result["success"]:
        # Look for the generated chart file
        chart_file = Path(output_dir) / "chart.png"
        if chart_file.exists():
            chart_path = str(chart_file)
            with open(chart_file, "rb") as f:
                chart_base64 = base64.b64encode(f.read()).decode("utf-8")
            logger.info("chart_tool.success", path=chart_path)
        else:
            # Check for any PNG file
            pngs = list(Path(output_dir).glob("*.png"))
            if pngs:
                chart_path = str(pngs[0])
                with open(pngs[0], "rb") as f:
                    chart_base64 = base64.b64encode(f.read()).decode("utf-8")

    return {
        "success": result["success"],
        "chart_base64": chart_base64,
        "chart_path": chart_path,
        "description": chart_description,
        "error": result.get("error"),
        "stderr": result.get("stderr"),
    }
