"""Contrats du registre canonique de dette technique."""

from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import audit_technical_debt as audit  # noqa: E402


def test_real_registry_covers_the_consolidated_audit_and_matches_the_document():
    data = audit.load_registry()
    items = audit.validate_registry(data)

    assert len(items) == 41
    assert {item["id"] for item in items} == audit.EXPECTED_IDS
    assert {item["id"] for item in items if item["status"] == "active"} == {
        "TD-P0-01",
        "TD-P1-06",
        "TD-P1-07",
    }
    assert audit.DOCUMENT_PATH.read_text(encoding="utf-8") == audit.render_document(
        data,
        items,
    )


def test_registry_rejects_a_resolution_without_existing_evidence():
    data = deepcopy(audit.load_registry())
    data["items"][0]["evidence"] = ["does/not/exist.py"]

    with pytest.raises(audit.RegistryError, match="preuve absente"):
        audit.validate_registry(data)


def test_registry_rejects_an_active_debt_without_owner_or_next_action():
    data = deepcopy(audit.load_registry())
    active = next(item for item in data["items"] if item["status"] == "active")
    active.pop("owner")

    with pytest.raises(audit.RegistryError, match="owner est obligatoire"):
        audit.validate_registry(data)
