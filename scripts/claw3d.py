#!/usr/bin/env python3
"""Gestionnaire local et optionnel de l'UI visuelle Claw3D.

Claw3D reste un dépôt autonome : aucun module métier JARVIS ne l'importe et
JARVIS ne dépend pas de sa présence pour fonctionner. Le superviseur peut
optionnellement piloter son cycle de vie (start/stop) via ce gestionnaire —
sans installer de LaunchAgent dédié ni coupler le code source.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Sequence
from urllib.parse import urlsplit, urlunsplit


JARVIS_ROOT = Path(__file__).resolve().parents[1]
CLAW3D_REPOSITORY = "https://github.com/AVTAVANTTOUT2/Claw3D.git"
CLAW3D_COMMIT = "202feaf0efd8ae92451368d408e387a507da0192"
CLAW3D_MARKER = "claw3d.visual-ui.root.v1"
VISUAL_TOKEN_RELATIVE_PATH = Path(".claw3d/auth/jarvis-visual.token")
VISUAL_CA_RELATIVE_PATH = Path(".claw3d/trust/jarvis-ca.pem")

Runner = Callable[[Sequence[str], Path | None], None]


class Claw3DError(RuntimeError):
    """Erreur de configuration ou de sécurité exploitable par le CLI."""


def _run(command: Sequence[str], cwd: Path | None = None) -> None:
    subprocess.run(list(command), cwd=cwd, check=True)


def _capture(command: Sequence[str], cwd: Path | None = None) -> str:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise Claw3DError(f"outil requis introuvable: {name}")


def apps_root(jarvis_root: Path = JARVIS_ROOT) -> Path:
    return jarvis_root.resolve() / ".jarvis" / "apps"


def claw3d_root(jarvis_root: Path = JARVIS_ROOT) -> Path:
    return apps_root(jarvis_root) / "claw3d"


def _write_private_file(path: Path, content: str, *, preserve: bool) -> Path:
    """Écrit un fichier régulier 0600 sans suivre de lien symbolique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise Claw3DError(f"chemin privé ambigu: {path}")
    if path.exists():
        if not path.is_file():
            raise Claw3DError(f"fichier privé invalide: {path}")
        path.chmod(0o600)
        if preserve:
            return path
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise Claw3DError(f"fichier temporaire ambigu: {temporary}")
    try:
        with temporary.open("x", encoding="ascii") as handle:
            handle.write(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
    return path


def provision_visual_credentials(root: Path, jarvis_origin: str) -> tuple[Path, Path | None]:
    """Crée le jeton scoped et copie uniquement le certificat public utile."""
    root = root.resolve()
    state_root = root / ".claw3d"
    if state_root.is_symlink():
        raise Claw3DError("le répertoire privé Claw3D ne peut pas être un lien symbolique")
    state_root.mkdir(exist_ok=True)
    token_path = root / VISUAL_TOKEN_RELATIVE_PATH
    if token_path.exists():
        token = token_path.read_text(encoding="ascii").strip()
        if not 43 <= len(token) <= 128 or not all(
            char.isalnum() or char in "_-" for char in token
        ):
            raise Claw3DError("jeton visual:read existant invalide")
        _write_private_file(token_path, token + "\n", preserve=True)
    else:
        _write_private_file(token_path, secrets.token_urlsafe(48) + "\n", preserve=False)

    ca_path: Path | None = None
    if urlsplit(jarvis_origin).scheme.lower() == "https":
        jarvis_root = root.parents[2]
        source = jarvis_root / "certs" / "cert.pem"
        if source.is_symlink() or not source.is_file():
            raise Claw3DError(
                "certificat JARVIS local introuvable; générez certs/cert.pem avant Claw3D"
            )
        ca_path = root / VISUAL_CA_RELATIVE_PATH
        _write_private_file(
            ca_path,
            source.read_text(encoding="ascii"),
            preserve=False,
        )
    return token_path, ca_path


def _ensure_local_directory(path: Path, parent: Path) -> None:
    resolved_parent = parent.resolve()
    try:
        relative = path.relative_to(parent)
    except ValueError as exc:
        raise Claw3DError(f"chemin extérieur à JarvisAPI refusé: {path}") from exc

    current = resolved_parent
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise Claw3DError(f"lien symbolique refusé: {current}")
        if current.exists():
            if not current.is_dir():
                raise Claw3DError(f"répertoire attendu: {current}")
        else:
            current.mkdir(mode=0o700)
    try:
        current.resolve().relative_to(resolved_parent)
    except ValueError as exc:  # pragma: no cover - garde contre une course filesystem.
        raise Claw3DError(f"chemin extérieur à JarvisAPI refusé: {path}") from exc


def _normalize_network_host(value: str, field: str) -> str:
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass

    hostname = candidate.removesuffix(".").lower()
    labels = hostname.split(".")
    valid = (
        bool(hostname)
        and hostname.isascii()
        and len(hostname) <= 253
        and all(
            label
            and len(label) <= 63
            and label[0] != "-"
            and label[-1] != "-"
            and all(char.isalnum() or char == "-" for char in label)
            for label in labels
        )
    )
    if not valid:
        raise Claw3DError(f"{field} contient un nom d'hôte invalide")
    return hostname


def normalize_jarvis_origin(value: str) -> str:
    """Valide une origine serveur sans credentials, chemin, query ou fragment."""

    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise Claw3DError("JARVIS_ORIGIN doit être une origine HTTP(S) non vide")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise Claw3DError("JARVIS_ORIGIN doit utiliser http:// ou https://")
    if parsed.username is not None or parsed.password is not None:
        raise Claw3DError("les credentials sont interdits dans JARVIS_ORIGIN")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise Claw3DError("JARVIS_ORIGIN ne doit contenir ni chemin, query ni fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise Claw3DError("port invalide dans JARVIS_ORIGIN") from exc
    hostname = _normalize_network_host(parsed.hostname, "JARVIS_ORIGIN")
    if hostname != "localhost":
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise Claw3DError("JARVIS_ORIGIN doit rester sur l'interface loopback")
    formatted_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = formatted_host + (f":{port}" if port is not None else "")
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def render_configuration(
    mode: str,
    jarvis_origin: str | None,
    host: str,
    port: int,
    *,
    visual_token_file: Path | None = None,
    ca_cert_file: Path | None = None,
) -> str:
    if mode not in {"mock", "null", "jarvis-readonly"}:
        raise Claw3DError(f"adaptateur visuel inconnu: {mode}")
    host = _normalize_network_host(host, "CLAW3D_HOST")
    if not 1024 <= port <= 65535:
        raise Claw3DError("CLAW3D_PORT doit être compris entre 1024 et 65535")

    connector_enabled = mode == "jarvis-readonly"
    if connector_enabled:
        origin = normalize_jarvis_origin(jarvis_origin or "")
    elif jarvis_origin:
        raise Claw3DError("--jarvis-origin est réservé au mode jarvis-readonly")
    else:
        origin = ""

    lines = [
        "# Généré explicitement par JarvisAPI scripts/claw3d.py.",
        "# Ce fichier ne contient aucun secret JARVIS.",
        f"VISUAL_ADAPTER={mode}",
        f"JARVIS_CONNECTOR_ENABLED={'true' if connector_enabled else 'false'}",
        f"JARVIS_ORIGIN={origin}",
    ]
    if connector_enabled:
        token_file = str(visual_token_file.resolve()) if visual_token_file else ""
        ca_file = str(ca_cert_file.resolve()) if ca_cert_file else ""
        lines.extend(
            (
                f"JARVIS_VISUAL_TOKEN_FILE={token_file}",
                f"NODE_EXTRA_CA_CERTS={ca_file}",
            )
        )
    lines.extend(
        (
            "VISUAL_BROWSER_PERSISTENCE=false",
            f"CLAW3D_HOST={host}",
            f"CLAW3D_PORT={port}",
            "",
        )
    )
    return "\n".join(lines)


def write_configuration(root: Path, content: str, *, replace: bool) -> bool:
    env_path = root / ".env"
    if env_path.is_symlink():
        raise Claw3DError("le fichier Claw3D .env ne peut pas être un lien symbolique")
    if env_path.exists() and not env_path.is_file():
        raise Claw3DError("le chemin Claw3D .env doit être un fichier régulier")
    if env_path.exists() and not replace:
        return False
    temporary = root / f".env.tmp-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        raise Claw3DError(f"fichier temporaire ambigu: {temporary}")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(content)
    temporary.chmod(0o600)
    temporary.replace(env_path)
    return True


def validate_installation(
    root: Path,
    *,
    expected_parent: Path | None = None,
    verify_commit: bool = True,
) -> None:
    expected_parent = (expected_parent or apps_root()).resolve()
    if root.is_symlink() or not root.is_dir():
        raise Claw3DError(f"installation Claw3D invalide: {root}")
    try:
        root.resolve().relative_to(expected_parent)
    except ValueError as exc:
        raise Claw3DError("installation Claw3D extérieure à .jarvis/apps") from exc

    marker = root / ".claw3d-root"
    package = root / "package.json"
    if marker.is_symlink() or not marker.is_file():
        raise Claw3DError("marqueur .claw3d-root absent ou ambigu")
    if marker.read_text(encoding="utf-8").strip() != CLAW3D_MARKER:
        raise Claw3DError("marqueur .claw3d-root invalide")
    if package.is_symlink() or not package.is_file():
        raise Claw3DError("package.json Claw3D absent ou ambigu")
    try:
        package_name = json.loads(package.read_text(encoding="utf-8")).get("name")
    except (json.JSONDecodeError, OSError) as exc:
        raise Claw3DError("package.json Claw3D illisible") from exc
    if package_name != "claw3d":
        raise Claw3DError("identité package.json Claw3D invalide")

    for name in ("install.sh", "start.sh", "stop.sh", "uninstall.sh", "verify-containment.sh"):
        script = root / "scripts" / name
        if script.is_symlink() or not script.is_file():
            raise Claw3DError(f"script lifecycle Claw3D absent ou ambigu: {name}")

    if verify_commit:
        current_commit = _capture(("git", "rev-parse", "HEAD"), cwd=root)
        if current_commit != CLAW3D_COMMIT:
            raise Claw3DError(
                f"version Claw3D inattendue: {current_commit}; attendue: {CLAW3D_COMMIT}"
            )


def _clone_pinned_claw3d(target: Path, runner: Runner = _run) -> None:
    if target.exists() or target.is_symlink():
        raise Claw3DError(f"cible d'installation Claw3D déjà présente: {target}")
    _require_tool("git")
    parent = target.parent
    with tempfile.TemporaryDirectory(prefix=".claw3d-install-", dir=parent) as temporary:
        checkout = Path(temporary) / "checkout"
        runner(
            (
                "git",
                "clone",
                "--filter=blob:none",
                "--no-tags",
                "--no-checkout",
                CLAW3D_REPOSITORY,
                str(checkout),
            ),
            None,
        )
        runner(("git", "checkout", "--detach", CLAW3D_COMMIT), checkout)
        validate_installation(checkout, expected_parent=parent)
        checkout.replace(target)


def configure(
    root: Path,
    *,
    expected_parent: Path | None = None,
    mode: str,
    jarvis_origin: str | None,
    host: str,
    port: int,
    replace: bool,
) -> bool:
    validate_installation(root, expected_parent=expected_parent)
    origin = normalize_jarvis_origin(jarvis_origin or "") if mode == "jarvis-readonly" else None
    token_file: Path | None = None
    ca_file: Path | None = None
    if origin is not None:
        token_file, ca_file = provision_visual_credentials(root, origin)
    content = render_configuration(
        mode,
        origin,
        host,
        port,
        visual_token_file=token_file,
        ca_cert_file=ca_file,
    )
    return write_configuration(root, content, replace=replace)


def install(
    jarvis_root: Path,
    *,
    mode: str,
    jarvis_origin: str | None,
    host: str,
    port: int,
    runner: Runner = _run,
) -> Path:
    for tool in ("git", "node", "npm"):
        _require_tool(tool)

    root = claw3d_root(jarvis_root)
    app_parent = apps_root(jarvis_root)
    _ensure_local_directory(app_parent, jarvis_root.resolve())

    if root.is_symlink():
        raise Claw3DError(f"lien symbolique Claw3D refusé: {root}")
    fresh = not root.exists()
    if fresh:
        _clone_pinned_claw3d(root, runner=runner)
    validate_installation(root, expected_parent=app_parent)

    created = configure(
        root,
        expected_parent=app_parent,
        mode=mode,
        jarvis_origin=jarvis_origin,
        host=host,
        port=port,
        replace=False,
    )
    if not created:
        print(f"Configuration existante conservée: {root / '.env'}")
        print("Utilisez la commande configure pour la remplacer explicitement.")

    runner((str(root / "scripts" / "install.sh"),), root)
    return root


def run_lifecycle(
    jarvis_root: Path,
    script_name: str,
    arguments: Sequence[str] = (),
    *,
    runner: Runner = _run,
) -> None:
    root = claw3d_root(jarvis_root)
    validate_installation(root, expected_parent=apps_root(jarvis_root))
    script = root / "scripts" / script_name
    runner((str(script), *arguments), root)


def is_installed(jarvis_root: Path = JARVIS_ROOT) -> bool:
    """True si le checkout Claw3D épinglé est présent et valide."""

    root = claw3d_root(jarvis_root)
    if not root.exists():
        return False
    try:
        validate_installation(
            root,
            expected_parent=apps_root(jarvis_root),
            verify_commit=False,
        )
    except Claw3DError:
        return False
    return True


def running_pid(jarvis_root: Path = JARVIS_ROOT) -> int | None:
    """PID du serveur Claw3D identifié, sinon None.

    Un simple ``kill(pid, 0)`` ne suffit pas : un PID périmé peut avoir été
    réattribué à un autre processus. Les mêmes preuves que ``stop.sh`` sont
    donc vérifiées avant de considérer le service comme actif.
    """

    root = claw3d_root(jarvis_root)
    state_file = root / ".claw3d" / "run" / "claw3d.state"
    if not state_file.is_file() or state_file.is_symlink():
        return None
    try:
        values: dict[str, str] = {}
        for line in state_file.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in values:
                return None
            values[key] = value
        pid = int(values.get("pid", ""))
        if pid <= 0 or values.get("root") != str(root.resolve()):
            return None
        stored_start = values.get("started", "")
        if not stored_start:
            return None
        os.kill(pid, 0)
        current_start = _capture(("ps", "-p", str(pid), "-o", "lstart="))
        command_line = _capture(("ps", "-p", str(pid), "-o", "command="))
        if current_start != stored_start:
            return None
        if "next-server" not in command_line and not (
            "next" in command_line and "start" in command_line
        ):
            return None
        return pid
    except (OSError, subprocess.CalledProcessError, TypeError, ValueError):
        return None


def is_running(jarvis_root: Path = JARVIS_ROOT) -> bool:
    return running_pid(jarvis_root) is not None


def sync_managed_configuration(
    jarvis_root: Path,
    *,
    mode: str,
    jarvis_origin: str,
    host: str,
    port: int,
) -> None:
    """Réécrit la config Claw3D pour coller à l'origine JARVIS courante."""

    root = claw3d_root(jarvis_root)
    configure(
        root,
        expected_parent=apps_root(jarvis_root),
        mode=mode,
        jarvis_origin=jarvis_origin,
        host=host,
        port=port,
        replace=True,
    )


def status(jarvis_root: Path) -> None:
    root = claw3d_root(jarvis_root)
    if not root.exists():
        print(f"Claw3D non installé ({root})")
        return
    validate_installation(root, expected_parent=apps_root(jarvis_root))
    pid = running_pid(jarvis_root)
    running = pid is not None
    print(f"Claw3D installé: {root}")
    print(f"Version épinglée: {CLAW3D_COMMIT}")
    print(f"État: {'actif' if running else 'arrêté'}" + (f" (PID {pid})" if running else ""))


def _add_configuration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=("mock", "null", "jarvis-readonly"),
        default="mock",
        help="adaptateur visuel (mock par défaut)",
    )
    parser.add_argument(
        "--jarvis-origin",
        help="origine HTTP(S) JARVIS, obligatoire uniquement en jarvis-readonly",
    )
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute Claw3D")
    parser.add_argument("--port", type=int, default=3000, help="port Claw3D")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Déployer l'UI visuelle Claw3D sans coupler le runtime JARVIS."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="cloner et installer Claw3D")
    _add_configuration_arguments(install_parser)

    configure_parser = subparsers.add_parser(
        "configure", help="remplacer explicitement la configuration visuelle"
    )
    _add_configuration_arguments(configure_parser)

    subparsers.add_parser("start", help="démarrer Claw3D")
    subparsers.add_parser("stop", help="arrêter Claw3D")
    subparsers.add_parser("status", help="afficher la version et l'état")
    subparsers.add_parser("verify", help="vérifier le confinement Claw3D")

    clean_parser = subparsers.add_parser(
        "clean", help="supprimer uniquement les artefacts régénérables"
    )
    clean_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser(
        "remove-source",
        help="déléguer la suppression interactive complète au script Claw3D",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            root = install(
                JARVIS_ROOT,
                mode=args.mode,
                jarvis_origin=args.jarvis_origin,
                host=args.host,
                port=args.port,
            )
            print(f"Claw3D prêt: {root}")
        elif args.command == "configure":
            root = claw3d_root()
            configure(
                root,
                expected_parent=apps_root(),
                mode=args.mode,
                jarvis_origin=args.jarvis_origin,
                host=args.host,
                port=args.port,
                replace=True,
            )
            print(f"Configuration remplacée explicitement: {root / '.env'}")
        elif args.command == "start":
            run_lifecycle(JARVIS_ROOT, "start.sh")
        elif args.command == "stop":
            run_lifecycle(JARVIS_ROOT, "stop.sh")
        elif args.command == "status":
            status(JARVIS_ROOT)
        elif args.command == "verify":
            run_lifecycle(JARVIS_ROOT, "verify-containment.sh")
        elif args.command == "clean":
            run_lifecycle(
                JARVIS_ROOT,
                "uninstall.sh",
                ("--dry-run",) if args.dry_run else (),
            )
        elif args.command == "remove-source":
            run_lifecycle(JARVIS_ROOT, "uninstall.sh", ("--remove-source",))
        else:  # pragma: no cover - argparse verrouille les commandes.
            raise Claw3DError(f"commande inconnue: {args.command}")
    except (Claw3DError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Erreur Claw3D: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
