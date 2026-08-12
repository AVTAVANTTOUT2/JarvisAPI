#!/usr/bin/env python3
"""CLI idempotent d'administration locale du provider OpenCode."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.opencode.adapter import stop_isolated_run_processes  # noqa: E402
from integrations.opencode.config import (  # noqa: E402
    OpenCodeSettings,
    RuntimeLayout,
    load_settings,
    provision_runtime_config,
    write_settings,
)
from integrations.opencode.lifecycle import (
    InstallManager,
    OpenCodeProcessManager,
    ReleaseManifest,
)  # noqa: E402
from integrations.opencode.lifecycle.install import InstallationError  # noqa: E402
from integrations.opencode.lifecycle.process import ProcessManagerError  # noqa: E402
from integrations.opencode.security.environment import EnvironmentSecurityError  # noqa: E402
from integrations.opencode.security.paths import PathSecurityError  # noqa: E402


def _emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)


def _components() -> tuple[
    RuntimeLayout,
    OpenCodeSettings,
    ReleaseManifest,
    InstallManager,
    OpenCodeProcessManager,
]:
    layout = RuntimeLayout.default()
    settings = load_settings(layout)
    manifest = ReleaseManifest.load()
    installer = InstallManager(layout=layout, settings=settings, manifest=manifest)
    process = OpenCodeProcessManager(
        layout=layout,
        settings=settings,
        manifest=manifest,
        install_manager=installer,
    )
    return layout, settings, manifest, installer, process


def _environment_from_names(
    names: Sequence[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    explicit: dict[str, str] = {}
    for name in names:
        if not name or "=" in name or "\x00" in name:
            raise EnvironmentSecurityError(f"Nom de variable invalide: {name!r}")
        if name not in os.environ:
            raise EnvironmentSecurityError(
                f"Variable explicitement demandée mais absente: {name}"
            )
        explicit[name] = os.environ[name]
    return explicit, tuple(explicit)


def command_install(args: argparse.Namespace) -> dict[str, Any]:
    _, _, manifest, installer, _ = _components()
    result = installer.install(
        archive_path=Path(args.archive) if args.archive else None,
        platform_key=args.platform,
        verify_binary=not args.skip_binary_check,
    )
    return {
        "action": "install",
        "asset": result.asset_key,
        "binary": str(result.binary_path),
        "changed": result.changed,
        "ok": True,
        "version": manifest.version,
    }


def command_configure(args: argparse.Namespace) -> dict[str, Any]:
    layout = RuntimeLayout.default()
    current = load_settings(layout)
    updates = {
        key: value
        for key, value in {
            "username": args.username,
            "startup_timeout_seconds": args.startup_timeout,
            "shutdown_timeout_seconds": args.shutdown_timeout,
            "request_timeout_seconds": args.request_timeout,
        }.items()
        if value is not None
    }
    settings = replace(current, **updates)
    manager_path = write_settings(settings, layout)
    opencode_path = provision_runtime_config(layout)
    return {
        "action": "configure",
        "manager_config": str(manager_path),
        "ok": True,
        "opencode_config": str(opencode_path),
    }


def _start(process: OpenCodeProcessManager, args: argparse.Namespace) -> dict[str, Any]:
    explicit, allowlist = _environment_from_names(args.allow_env or ())
    state = process.start(
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
        explicit_environment=explicit,
        additional_environment_allowlist=allowlist,
    )
    return {
        "action": "start",
        "base_url": state.base_url,
        "ok": True,
        "pid": state.pid,
        "version": state.version,
    }


def command_start(args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    return _start(process, args)


def command_stop(_args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    changed = process.stop()
    return {"action": "stop", "changed": changed, "ok": True}


def command_restart(args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    process.stop()
    result = _start(process, args)
    result["action"] = "restart"
    return result


def command_status(_args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    status = process.status()
    return {"action": "status", "ok": True, **asdict(status)}


def command_health(_args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    report = process.health()
    return {"action": "health", "ok": report.healthy, **asdict(report)}


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    installer = _components()[3]
    report = installer.verify(execute_binary=not args.skip_execute)
    return {
        "action": "verify",
        "ok": report.valid,
        **asdict(report),
        "binary_path": str(report.binary_path),
    }


def command_smoke_test(args: argparse.Namespace) -> dict[str, Any]:
    process = _components()[4]
    status = process.status()
    started_here = not status.running
    if started_here:
        _start(process, args)
    try:
        report = process.health()
        return {
            "action": "smoke-test",
            "healthy": report.healthy,
            "ok": report.healthy,
            "version": report.version,
        }
    finally:
        if started_here:
            process.stop()


def command_clean(_args: argparse.Namespace) -> dict[str, Any]:
    components = _components()
    installer, process = components[3], components[4]
    if process.status().running:
        raise ProcessManagerError("Arrête OpenCode avant clean")
    installer.clean()
    return {"action": "clean", "ok": True}


def command_uninstall(_args: argparse.Namespace) -> dict[str, Any]:
    components = _components()
    layout, settings, manifest, installer, process = components
    stop_isolated_run_processes(
        layout=layout,
        settings=settings,
        manifest=manifest,
        install_manager=installer,
    )
    process.stop()
    changed = installer.uninstall()
    return {"action": "uninstall", "changed": changed, "ok": True}


def command_print_version(_args: argparse.Namespace) -> dict[str, Any]:
    components = _components()
    manifest, installer = components[2], components[3]
    report = installer.verify(execute_binary=False)
    return {
        "action": "print-version",
        "installed": report.valid,
        "ok": True,
        "tag": manifest.tag,
        "version": manifest.version,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gestionnaire local OpenCode pour JARVIS"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument(
        "--archive", help="Archive locale vérifiée; sinon téléchargement officiel"
    )
    install.add_argument(
        "--platform",
        choices=(
            "darwin-arm64",
            "darwin-x64",
            "linux-arm64",
            "linux-x64",
            "windows-x64",
        ),
    )
    install.add_argument(
        "--skip-binary-check", action="store_true", help=argparse.SUPPRESS
    )
    install.set_defaults(handler=command_install)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--username")
    configure.add_argument("--startup-timeout", type=float)
    configure.add_argument("--shutdown-timeout", type=float)
    configure.add_argument("--request-timeout", type=float)
    configure.set_defaults(handler=command_configure)

    def add_start_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace")
        command.add_argument(
            "--allow-env",
            action="append",
            default=[],
            metavar="NAME",
            help="Transmettre explicitement une variable existante, sans afficher sa valeur",
        )

    start = subparsers.add_parser("start")
    add_start_options(start)
    start.set_defaults(handler=command_start)

    stop = subparsers.add_parser("stop")
    stop.set_defaults(handler=command_stop)

    restart = subparsers.add_parser("restart")
    add_start_options(restart)
    restart.set_defaults(handler=command_restart)

    status = subparsers.add_parser("status")
    status.set_defaults(handler=command_status)

    health = subparsers.add_parser("health")
    health.set_defaults(handler=command_health)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--skip-execute", action="store_true", help=argparse.SUPPRESS)
    verify.set_defaults(handler=command_verify)

    smoke = subparsers.add_parser("smoke-test")
    add_start_options(smoke)
    smoke.set_defaults(handler=command_smoke_test)

    clean = subparsers.add_parser("clean")
    clean.set_defaults(handler=command_clean)

    uninstall = subparsers.add_parser("uninstall")
    uninstall.set_defaults(handler=command_uninstall)

    print_version = subparsers.add_parser("print-version")
    print_version.set_defaults(handler=command_print_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = args.handler(args)
    except (
        EnvironmentSecurityError,
        InstallationError,
        PathSecurityError,
        ProcessManagerError,
        ValueError,
    ) as exc:
        _emit(
            {
                "command": args.command,
                "error": type(exc).__name__,
                "message": str(exc),
                "ok": False,
            },
            stream=sys.stderr,
        )
        return 1
    _emit(payload)
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
