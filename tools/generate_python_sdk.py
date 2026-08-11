#!/usr/bin/env python3
"""Génère le registre d'opérations du SDK Python depuis l'OpenAPI canonique."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "openapi" / "jarvis.openapi.json"
DEFAULT_OUTPUT = ROOT / "sdk" / "python" / "src" / "jarvis_sdk" / "operations.py"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SUPPORTED_AUTH_KINDS = {
    "device_token",
    "mobile_bearer",
    "mobile_or_location_token",
    "pairing_code",
    "public",
    "session",
    "session_or_mobile",
}


def _operations(schema: dict[str, Any]) -> list[dict[str, str]]:
    operations: list[dict[str, str]] = []
    seen: set[str] = set()
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            auth = operation.get("x-jarvis-authentication")
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError(f"operationId absent : {method.upper()} {path}")
            if operation_id in seen:
                raise ValueError(f"operationId dupliqué : {operation_id}")
            if not isinstance(auth, str) or not auth:
                raise ValueError(f"auth absente : {operation_id}")
            if auth not in SUPPORTED_AUTH_KINDS:
                raise ValueError(f"auth non supportée par le SDK : {operation_id} ({auth})")
            seen.add(operation_id)
            operations.append(
                {
                    "operation_id": operation_id,
                    "method": method.upper(),
                    "path": path,
                    "auth": auth,
                    "tag": str((operation.get("tags") or ["other"])[0]),
                    "summary": str(operation.get("summary") or ""),
                }
            )
    return sorted(operations, key=lambda item: item["operation_id"])


def render_operations(schema: dict[str, Any]) -> str:
    version = str(schema.get("info", {}).get("version") or "")
    if not version:
        raise ValueError("Version OpenAPI absente")
    lines = [
        '"""Généré par tools/generate_python_sdk.py — ne pas éditer."""',
        "",
        "from types import MappingProxyType",
        "from typing import Mapping",
        "",
        "from .models import Operation",
        "",
        f"CONTRACT_VERSION = {version!r}",
        "",
        "_OPERATIONS = {",
    ]
    for item in _operations(schema):
        lines.extend(
            [
                f"    {item['operation_id']!r}: Operation(",
                f"        operation_id={item['operation_id']!r},",
                f"        method={item['method']!r},",
                f"        path={item['path']!r},",
                f"        auth={item['auth']!r},",
                f"        tag={item['tag']!r},",
                f"        summary={item['summary']!r},",
                "    ),",
            ]
        )
    lines.extend(
        [
            "}",
            "",
            "OPERATIONS: Mapping[str, Operation] = MappingProxyType(_OPERATIONS)",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    schema_path = args.schema.expanduser().resolve()
    output = args.output.expanduser().resolve()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    rendered = render_operations(schema)

    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print(f"[generate_python_sdk] registre obsolète : {output}")
            return 1
        print(f"[generate_python_sdk] registre synchronisé : {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"[generate_python_sdk] écrit : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
