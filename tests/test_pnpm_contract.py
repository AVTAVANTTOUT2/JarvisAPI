"""Contrat de version pnpm entre les manifests, les lockfiles et la CI."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNPM_VERSION = "11.11.0"
PNPM_PACKAGE_MANAGER = f"pnpm@{PNPM_VERSION}"


def test_pnpm_manifests_use_the_same_exact_version():
    for directory in ("frontend", "web"):
        manifest = json.loads((ROOT / directory / "package.json").read_text(encoding="utf-8"))
        assert manifest["packageManager"] == PNPM_PACKAGE_MANAGER


def test_ci_installs_the_manifest_pnpm_version_exactly():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count(f"version: {PNPM_VERSION}") == 3
    assert "version: 11\n" not in workflow


def test_pnpm_lockfiles_use_the_supported_v9_format():
    for directory in ("frontend", "web"):
        lockfile = (ROOT / directory / "pnpm-lock.yaml").read_text(encoding="utf-8")
        assert lockfile.startswith("lockfileVersion: '9.0'\n")
