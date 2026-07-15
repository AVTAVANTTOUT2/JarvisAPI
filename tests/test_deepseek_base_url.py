"""Normalisation DEEPSEEK_BASE_URL — évite /v1/v1/ dans les appels llm.py."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _reload_config(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)
    import config

    importlib.reload(config)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://api.deepseek.com", "https://api.deepseek.com"),
        ("https://api.deepseek.com/", "https://api.deepseek.com"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com"),
        ("https://api.deepseek.com/v1/", "https://api.deepseek.com"),
    ],
)
def test_deepseek_base_url_strips_trailing_v1(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    _reload_config(monkeypatch, raw)
    import config

    assert config.DEEPSEEK_BASE_URL == expected
    assert f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions" == (
        "https://api.deepseek.com/v1/chat/completions"
    )
