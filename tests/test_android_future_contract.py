"""Contrat des placeholders Android : backlog explicite, aucun faux feature flag."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID_MAIN = ROOT / "android" / "app" / "src" / "main" / "kotlin"
FUTURE_REGISTRY = ROOT / "android" / "docs" / "FUTURE_FEATURES.md"
BACKLOG_ID = re.compile(r"JARVIS-FUTURE-[A-Z-]+")


def test_android_future_placeholders_have_no_runtime_activation_flag() -> None:
    sources = [path.read_text(encoding="utf-8") for path in ANDROID_MAIN.rglob("*.kt")]

    assert not (
        ANDROID_MAIN / "fr" / "jarvis" / "companion" / "core" / "JarvisFeatureFlags.kt"
    ).exists()
    assert all("JarvisFeatureFlags" not in source for source in sources)
    assert all("futureFlagId" not in source for source in sources)


def test_android_future_backlog_ids_match_the_documented_registry() -> None:
    code_ids = {
        match
        for path in ANDROID_MAIN.rglob("*.kt")
        for match in BACKLOG_ID.findall(path.read_text(encoding="utf-8"))
    }
    documented_ids = set(
        BACKLOG_ID.findall(FUTURE_REGISTRY.read_text(encoding="utf-8"))
    )

    assert code_ids == documented_ids
