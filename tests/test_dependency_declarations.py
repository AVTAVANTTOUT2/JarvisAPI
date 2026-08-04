"""Contrats des dépendances importées par les outils audités."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = tuple(
    path for path in ROOT.glob("requirements*.txt") if path.name != "requirements-ci.txt"
)


def _declaring_files(package: str) -> list[Path]:
    pattern = re.compile(rf"^{re.escape(package)}(?:\[.*\])?(?:[<>=!~].*)?$", re.IGNORECASE)
    matches: list[Path] = []
    for path in REQUIREMENTS:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if pattern.match(line):
                matches.append(path)
    return matches


def test_aiohttp_is_declared_once_for_tv_mcp_runtime() -> None:
    assert _declaring_files("aiohttp") == [ROOT / "requirements.txt"]


def test_mlx_audio_is_declared_once_in_the_separate_mlx_venv() -> None:
    assert _declaring_files("mlx-audio") == [ROOT / "requirements-mlx.txt"]
    mlx_requirements = (ROOT / "requirements-mlx.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" not in mlx_requirements


def test_httpx2_is_declared_only_in_test_profiles() -> None:
    """Starlette TestClient needs httpx2; the production server does not."""

    assert _declaring_files("httpx2") == [ROOT / "requirements-dev.txt"]
    ci_requirements = (ROOT / "requirements-ci.txt").read_text(encoding="utf-8")
    production_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "httpx2==2.9.*" in ci_requirements
    assert "httpx2" not in production_requirements
