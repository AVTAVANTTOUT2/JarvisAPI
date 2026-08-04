"""Contrats anti-duplication des runtimes frontend canoniques."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORED_PARTS = {".next", "dist", "node_modules", "out"}


def _source_files(*roots: str, suffixes: set[str]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        for path in (ROOT / root).rglob("*"):
            if (
                path.is_file()
                and path.suffix in suffixes
                and not IGNORED_PARTS.intersection(path.relative_to(ROOT).parts)
            ):
                files.append(path)
    return files


def test_only_one_service_worker_source_is_shipped() -> None:
    candidates = {
        "sw.js",
        "sw.ts",
        "service-worker.js",
        "service-worker.ts",
        "serviceWorker.js",
        "serviceWorker.ts",
    }
    sources = [
        path.relative_to(ROOT).as_posix()
        for path in _source_files("frontend", "web", "web_mobile", suffixes={".js", ".ts"})
        if path.name in candidates
    ]
    assert sorted(sources) == ["frontend/public/sw.js"]


def test_maplibre_has_one_runtime_constructor() -> None:
    constructors: list[str] = []
    for path in _source_files("frontend/src", "web/src", suffixes={".ts", ".tsx"}):
        if "new maplibregl.Map(" in path.read_text(encoding="utf-8"):
            constructors.append(path.relative_to(ROOT).as_posix())
    assert constructors == ["web/src/app/components/map/CartographyMap.tsx"]


def test_relative_time_has_one_implementation() -> None:
    implementation_pattern = re.compile(
        r"\bfunction\s+(?:formatRelativeTime|relativeDate|relativeMinutes|timeAgo)\s*\("
    )
    implementations: list[tuple[str, str]] = []
    for path in _source_files(
        "frontend/src",
        "web/src",
        "web_mobile/js",
        suffixes={".js", ".ts", ".tsx"},
    ):
        for match in implementation_pattern.finditer(path.read_text(encoding="utf-8")):
            implementations.append(
                (path.relative_to(ROOT).as_posix(), match.group(0))
            )
    assert implementations == [
        ("frontend/src/lib/timeFormat.ts", "function formatRelativeTime("),
    ]
