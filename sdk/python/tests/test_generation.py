from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jarvis_sdk import CONTRACT_VERSION
from tools.generate_python_sdk import DEFAULT_OUTPUT, main, render_operations

ROOT = Path(__file__).resolve().parents[3]


def test_generated_registry_is_current() -> None:
    assert main(["--check"]) == 0
    assert DEFAULT_OUTPUT.is_file()


def test_package_version_matches_contract() -> None:
    package = tomllib.loads((ROOT / "sdk" / "python" / "pyproject.toml").read_text())
    assert package["project"]["version"] == CONTRACT_VERSION == "1.0.0"


def test_generator_rejects_an_unknown_auth_boundary() -> None:
    schema = {
        "info": {"version": "1.0.0"},
        "paths": {
            "/api/example": {
                "get": {
                    "operationId": "get_api_example",
                    "x-jarvis-authentication": "silent_fallback",
                }
            }
        },
    }
    with pytest.raises(ValueError, match="non supportée"):
        render_operations(schema)
