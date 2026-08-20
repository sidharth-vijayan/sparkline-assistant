"""
tests/test_ingestion_pipeline_wiring.py
────────────────────────────────────────
Every `self.<name>` the IngestionPipeline reaches for must actually exist.

Written after document ingestion was found broken on main: extracting
parse_and_chunk() to module level carried `_find_existing_document` out of the
class with it, where a four-space indent turned it into a nested function inside
that new module-level function. ingest() calls it on every upload, so every
single upload raised AttributeError — and the whole suite still passed, because
nothing exercised ingest().

An end-to-end ingest test needs Postgres, MinIO, Qdrant and the embedding model,
so it cannot run in the unit suite. This checks the same failure structurally
instead, with no infrastructure: read the class, collect what it calls on itself,
and confirm each one resolves. That covers the general fault — a method silently
leaving the class body — rather than only the one instance of it.

    poetry run pytest tests/test_ingestion_pipeline_wiring.py
"""

import ast
import inspect
from pathlib import Path

import pytest

from ingestion.pipeline import IngestionPipeline

PIPELINE_SOURCE = Path(inspect.getfile(IngestionPipeline))


def _class_node() -> ast.ClassDef:
    tree = ast.parse(PIPELINE_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "IngestionPipeline":
            return node
    pytest.fail("IngestionPipeline is no longer a top-level class in pipeline.py")


def _self_attributes_used(cls: ast.ClassDef) -> set[str]:
    """Names read off `self` anywhere in the class body."""
    used: set[str] = set()
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Load)
        ):
            used.add(node.attr)
    return used


def _self_attributes_assigned(cls: ast.ClassDef) -> set[str]:
    """Names written to `self` anywhere in the class body (i.e. instance state)."""
    assigned: set[str] = set()
    for node in ast.walk(cls):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)
    return assigned


def test_every_self_call_resolves_to_something_real():
    cls = _class_node()
    used = _self_attributes_used(cls)
    available = _self_attributes_assigned(cls) | set(dir(IngestionPipeline))

    missing = sorted(used - available)
    assert not missing, (
        f"IngestionPipeline uses self.{{{', '.join(missing)}}} but nothing defines "
        f"them. A method indented as if it were in the class can end up nested "
        f"inside another function instead — check the indentation of those names "
        f"in {PIPELINE_SOURCE.name}."
    )


def test_find_existing_document_is_a_method_of_the_class():
    """
    The specific regression. ingest() looks a document up by filename to decide
    whether an upload is a new document or a new version of an existing one, so
    losing this method breaks every upload rather than only re-uploads.
    """
    assert hasattr(IngestionPipeline, "_find_existing_document")
    assert inspect.iscoroutinefunction(IngestionPipeline._find_existing_document)


def test_the_methods_ingest_depends_on_are_all_bound():
    for name in ("ingest", "_parse_and_chunk", "_find_existing_document"):
        attr = getattr(IngestionPipeline, name, None)
        assert attr is not None, f"IngestionPipeline.{name} is missing"
        assert inspect.iscoroutinefunction(attr), f"{name} should be async"


def test_no_function_is_defined_inside_the_module_level_parser():
    """
    parse_and_chunk() is a dispatch table of parser calls. A `def` inside it is
    almost certainly a method that lost its class, which is exactly how the
    ingestion break happened and how it would happen again.
    """
    tree = ast.parse(PIPELINE_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "parse_and_chunk"
        ):
            nested = [
                child.name
                for child in ast.walk(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child is not node
            ]
            assert not nested, (
                f"parse_and_chunk() contains nested definitions {nested}. If one "
                f"of these takes `self`, it belongs in IngestionPipeline and is "
                f"currently unreachable."
            )
            return
    pytest.fail("parse_and_chunk() is no longer a module-level function")
