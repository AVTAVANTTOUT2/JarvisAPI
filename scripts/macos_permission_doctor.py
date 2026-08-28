"""Rapport TCC macOS — lecture seule, jamais de tccutil reset."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.macos_permissions import probe_macos_permissions  # noqa: E402


def main() -> int:
    print(json.dumps(probe_macos_permissions(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
