#!/usr/bin/env python3
"""Exporte ou vérifie le contrat OpenAPI public déterministe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT = ROOT / "openapi" / "jarvis.openapi.json"


def render_schema() -> str:
    import main

    return json.dumps(
        main.app.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    rendered = render_schema()

    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print(f"[export_openapi] contrat obsolète : {output}", file=sys.stderr)
            return 1
        print(f"[export_openapi] contrat synchronisé : {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"[export_openapi] écrit : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
