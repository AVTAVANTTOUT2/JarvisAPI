"""Démarre un vrai serveur OpenCode puis quitte pour simuler un restart JARVIS."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import cast

from integrations.opencode.adapter import _RunRuntimeLayout
from integrations.opencode.config import RuntimeLayout, load_settings
from integrations.opencode.lifecycle import (
    InstallManager,
    OpenCodeProcessManager,
    ReleaseManifest,
)


class _InstalledBinary:
    def verify(self, *, execute_binary: bool) -> SimpleNamespace:
        del execute_binary
        return SimpleNamespace(valid=True, version="1.18.16", errors=())


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    integration_root = Path(sys.argv[1]).resolve()
    run_root = Path(sys.argv[2]).resolve()
    workspace = Path(sys.argv[3]).resolve()
    shared_binary = Path(sys.argv[4]).resolve()
    base_layout = RuntimeLayout.from_integration_root(integration_root)
    settings = load_settings(base_layout)
    manifest = ReleaseManifest.load()
    run_layout = _RunRuntimeLayout(
        integration_root=integration_root,
        runtime_root=run_root,
        shared_binary_path=shared_binary,
    )
    manager = OpenCodeProcessManager(
        layout=run_layout,
        settings=settings,
        manifest=manifest,
        install_manager=cast(InstallManager, _InstalledBinary()),
    )
    state = manager.start(workspace=workspace)
    print(json.dumps({"pid": state.pid, "port": state.port}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
