"""
tools/sandbox.py
─────────────────
Sandboxed Python execution environment for LLM-generated code.

Used by the tool executor to run matplotlib/pandas chart generation and
data export code safely. The sandbox:
  - Restricts imports to an explicit allowlist
  - Runs in a subprocess with a timeout (no threading tricks)
  - Captures stdout, stderr, and any file outputs
  - Returns results without leaking the host filesystem

SECURITY NOTE: This sandbox is appropriate for an internal, trusted-user
deployment. It is NOT production-hardened against adversarial inputs.
For a public deployment, use a container-level sandbox (e.g., gVisor).
The allowlist approach here prevents accidental access, not deliberate attacks.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Modules explicitly allowed in sandboxed code
ALLOWED_IMPORTS = frozenset(
    [
        "matplotlib",
        "matplotlib.pyplot",
        "matplotlib.figure",
        "pandas",
        "numpy",
        "openpyxl",
        "docx",
        "io",
        "os.path",
        "json",
        "csv",
        "datetime",
        "math",
        "statistics",
        "collections",
        "itertools",
        "re",
        "string",
        "base64",
    ]
)

EXECUTION_TIMEOUT_SECONDS = 30


def _check_imports(code: str) -> list[str]:
    """
    Parse the code and return a list of disallowed imports.
    Returns empty list if all imports are allowed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Syntax error in generated code: {e}") from e

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if alias.name not in ALLOWED_IMPORTS and root_module not in ALLOWED_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if node.module not in ALLOWED_IMPORTS and root_module not in ALLOWED_IMPORTS:
                    violations.append(node.module)

    return violations


def execute_code(
    code: str,
    output_dir: Optional[str] = None,
) -> dict:
    """
    Execute sandboxed Python code in a subprocess.

    Args:
        code: Python code string to execute
        output_dir: Directory where generated files (charts, exports) should be saved.
                    If None, uses a temporary directory.

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "output_files": list[str],  # Absolute paths to generated files
            "error": str | None
        }
    """
    # ── Import safety check ────────────────────────────────────────
    violations = _check_imports(code)
    if violations:
        error_msg = f"Disallowed imports detected: {violations}"
        logger.warning("sandbox.blocked", violations=violations)
        return {
            "success": False,
            "stdout": "",
            "stderr": error_msg,
            "output_files": [],
            "error": error_msg,
        }

    # ── Set up output directory ────────────────────────────────────
    use_temp = output_dir is None
    if use_temp:
        tmp_dir = tempfile.mkdtemp(prefix="sparkline_sandbox_")
        output_dir = tmp_dir
    else:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── Wrap code with output directory injection ──────────────────
    wrapped_code = textwrap.dedent(f"""
import os
import sys
os.chdir({repr(output_dir)})
OUTPUT_DIR = {repr(output_dir)}

{code}
""")

    # ── Write to temp file and execute in subprocess ───────────────
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp_file:
        tmp_file.write(wrapped_code)
        tmp_path = tmp_file.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
            cwd=output_dir,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        success = result.returncode == 0

        # Collect any output files generated
        output_files = [
            str(Path(output_dir) / f)
            for f in os.listdir(output_dir)
            if not f.startswith(".")
        ]

        if not success:
            logger.warning(
                "sandbox.execution_error",
                returncode=result.returncode,
                stderr=stderr[:500],
            )
        else:
            logger.info(
                "sandbox.execution_success",
                output_files=output_files,
            )

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_files": output_files,
            "error": stderr if not success else None,
        }

    except subprocess.TimeoutExpired:
        error_msg = f"Code execution timed out after {EXECUTION_TIMEOUT_SECONDS}s"
        logger.error("sandbox.timeout")
        return {
            "success": False,
            "stdout": "",
            "stderr": error_msg,
            "output_files": [],
            "error": error_msg,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
