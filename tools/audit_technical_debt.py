#!/usr/bin/env python3
"""Valide et rend le registre canonique de dette technique JARVIS."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "Architecture" / "technical_debt_registry.json"
DOCUMENT_PATH = ROOT / "Architecture" / "23_TECHNICAL_DEBT.md"
ALLOWED_STATUSES = frozenset({"active", "resolved"})
ID_PATTERN = re.compile(r"TD-(P[012])-(\d{2})\Z")
EXPECTED_IDS = frozenset(
    [*(f"TD-P0-{index:02d}" for index in range(1, 9))]
    + [*(f"TD-P1-{index:02d}" for index in range(1, 20))]
    + [*(f"TD-P2-{index:02d}" for index in range(1, 15))]
)


class RegistryError(ValueError):
    """Le registre ne respecte pas son contrat canonique."""


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RegistryError("la racine du registre doit être un objet JSON")
    return data


def validate_registry(data: dict[str, Any], root: Path = ROOT) -> list[dict[str, Any]]:
    if data.get("schema_version") != 1:
        raise RegistryError("schema_version doit valoir 1")
    if not isinstance(data.get("updated_at"), str) or not data["updated_at"].strip():
        raise RegistryError("updated_at est obligatoire")

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise RegistryError("items doit être une liste")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_items, 1):
        if not isinstance(raw, dict):
            raise RegistryError(f"items[{position}] doit être un objet")
        item_id = raw.get("id")
        match = ID_PATTERN.fullmatch(item_id) if isinstance(item_id, str) else None
        if match is None:
            raise RegistryError(f"identifiant invalide à items[{position}]: {item_id!r}")
        if item_id in seen:
            raise RegistryError(f"identifiant dupliqué: {item_id}")
        seen.add(item_id)

        severity = raw.get("severity")
        if severity != match.group(1):
            raise RegistryError(f"{item_id}: severity doit valoir {match.group(1)}")
        status = raw.get("status")
        if status not in ALLOWED_STATUSES:
            raise RegistryError(f"{item_id}: statut inconnu {status!r}")
        if not isinstance(raw.get("summary"), str) or not raw["summary"].strip():
            raise RegistryError(f"{item_id}: summary est obligatoire")

        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise RegistryError(f"{item_id}: au moins une preuve est obligatoire")
        for relative in evidence:
            if not isinstance(relative, str) or not relative.strip():
                raise RegistryError(f"{item_id}: chemin de preuve invalide")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError as exc:
                raise RegistryError(f"{item_id}: preuve hors dépôt: {relative}") from exc
            if not candidate.exists():
                raise RegistryError(f"{item_id}: preuve absente: {relative}")

        required = "resolution" if status == "resolved" else "next_action"
        if not isinstance(raw.get(required), str) or not raw[required].strip():
            raise RegistryError(f"{item_id}: {required} est obligatoire")
        if status == "active" and (
            not isinstance(raw.get("owner"), str) or not raw["owner"].strip()
        ):
            raise RegistryError(f"{item_id}: owner est obligatoire pour une dette active")
        items.append(raw)

    missing = sorted(EXPECTED_IDS - seen)
    extra = sorted(seen - EXPECTED_IDS)
    if missing or extra:
        raise RegistryError(f"couverture invalide; absents={missing}, inattendus={extra}")
    return sorted(items, key=lambda item: item["id"])


def _evidence_links(paths: list[str]) -> str:
    return ", ".join(f"[`{path}`](../{path})" for path in paths)


def render_document(data: dict[str, Any], items: list[dict[str, Any]]) -> str:
    active = [item for item in items if item["status"] == "active"]
    resolved = [item for item in items if item["status"] == "resolved"]
    lines = [
        "# 23 — Registre canonique de dette technique",
        "",
        "<!-- Généré par tools/audit_technical_debt.py ; modifier le JSON source. -->",
        "",
        f"**Mise à jour :** {data['updated_at']} — **État :** {len(resolved)} "
        f"résolues, {len(active)} actives, {len(items)} suivies.",
        "",
        "La source de vérité est "
        "[`Architecture/technical_debt_registry.json`](technical_debt_registry.json). "
        "La CI valide les identifiants, les statuts, les preuves et ce rendu avec "
        "`python tools/audit_technical_debt.py --check`. Il n'existe pas de table "
        "SQLite `technical_debt` : la consigne historique correspondante est retirée.",
        "",
        "Les anciens identifiants `TD-001` à `TD-013`, tous soldés, restent dans "
        "l'historique Git. Le présent registre couvre exactement l'audit consolidé "
        "P0/P1/P2 postérieur aux audits P01–P18.",
        "",
        "## Dettes actives",
        "",
        "| ID | Dette | Propriétaire | Prochaine action | Preuves |",
        "|---|---|---|---|---|",
    ]
    for item in active:
        lines.append(
            f"| {item['id']} | {item['summary']} | {item['owner']} | "
            f"{item['next_action']} | {_evidence_links(item['evidence'])} |"
        )
    if not active:
        lines.append("| — | Aucune dette active | — | — | — |")

    lines.extend(
        [
            "",
            "## Dettes résolues",
            "",
            "| ID | Dette | Résolution vérifiable | Preuves |",
            "|---|---|---|---|",
        ]
    )
    for item in resolved:
        lines.append(
            f"| {item['id']} | {item['summary']} | {item['resolution']} | "
            f"{_evidence_links(item['evidence'])} |"
        )

    lines.extend(
        [
            "",
            "## Règles de gouvernance",
            "",
            "1. Une dette nouvelle reçoit un identifiant, un propriétaire, une action "
            "et au moins une preuve de code ou de test.",
            "2. `resolved` signifie qu'un contrat automatique existe ; une intention, "
            "une PR seule ou un test manuel non consigné ne suffisent pas.",
            "3. Toute dette P0 active bloque l'ajout de fonctionnalités sans rapport.",
            "4. Le registre JSON et ce document doivent être mis à jour dans le même commit.",
            "5. Les nombres de tables, routes et frontends viennent exclusivement de "
            "`artifacts/architecture_truth.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="échoue si le rendu diverge")
    parser.add_argument("--write", action="store_true", help="réécrit le document canonique")
    args = parser.parse_args(argv)
    if args.check == args.write:
        parser.error("choisir exactement --check ou --write")

    try:
        data = load_registry()
        items = validate_registry(data)
        rendered = render_document(data, items)
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        print(f"[audit_technical_debt] ERROR {exc}", file=sys.stderr)
        return 1

    if args.write:
        DOCUMENT_PATH.write_text(rendered, encoding="utf-8")
        print(f"[audit_technical_debt] écrit {DOCUMENT_PATH.relative_to(ROOT)}")
        return 0

    current = DOCUMENT_PATH.read_text(encoding="utf-8") if DOCUMENT_PATH.exists() else ""
    if current != rendered:
        print(
            "[audit_technical_debt] ERROR Architecture/23_TECHNICAL_DEBT.md "
            "diverge du registre",
            file=sys.stderr,
        )
        return 1
    print(f"[audit_technical_debt] OK {len(items)} dettes ({len([i for i in items if i['status'] == 'active'])} actives)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
