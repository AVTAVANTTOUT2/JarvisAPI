"""Fitness functions exécutables de la Phase 4."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from fastapi import APIRouter


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_phase4_assembles_exactly_fourteen_domain_routers():
    router_paths = sorted(API_DIR.glob("router_*.py"))

    assert len(router_paths) == 14
    for path in router_paths:
        module = importlib.import_module(f"api.{path.stem}")
        assert isinstance(module.router, APIRouter), path.name


def test_phase4_keeps_entrypoint_and_api_modules_under_500_lines():
    assert _line_count(ROOT / "main.py") < 500

    oversized = {
        path.name: _line_count(path)
        for path in API_DIR.glob("*.py")
        if _line_count(path) > 500
    }
    assert oversized == {}


def test_phase4_api_layer_never_imports_main():
    offenders: list[str] = []

    for path in API_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "main" for alias in node.names):
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and node.module == "main":
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []


def test_phase4_mounts_the_extracted_lifespan_unchanged():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))

    fastapi_calls = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "app" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "FastAPI"
    ]
    assert len(fastapi_calls) == 1

    lifespan_keyword = next(
        keyword for keyword in fastapi_calls[0].keywords if keyword.arg == "lifespan"
    )
    assert isinstance(lifespan_keyword.value, ast.Name)
    assert lifespan_keyword.value.id == "lifespan"
