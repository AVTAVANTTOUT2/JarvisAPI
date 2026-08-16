"""Paramètres du provider sans dépendance vers le cœur JARVIS.

Les valeurs persistées ne contiennent jamais le mot de passe du serveur. Celui-ci
est généré au démarrage et stocké séparément dans un fichier éphémère 0600.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat as stat_module
import tempfile
from typing import Any, Mapping


INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_FILE = Path(__file__).with_name("defaults.json")
OPENCODE_CONFIG_TEMPLATE = Path(__file__).with_name("opencode.json")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RUNTIME_OVERLAY_KEYS = frozenset({"mcp"})
_MCP_LOCAL_KEYS = frozenset({"type", "command", "environment", "enabled", "timeout"})
_MCP_ENVIRONMENT_KEYS = frozenset({"PYTHONPATH"})
_MAX_MCP_ARGUMENT_LENGTH = 8_192
_MAX_MCP_TIMEOUT_MS = 60_000


class ConfigurationError(ValueError):
    """La configuration locale est invalide ou sort de la frontière du plugin."""


def _path_is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    """Chemins runtime confinés sous ``integrations/opencode/.runtime``."""

    integration_root: Path
    runtime_root: Path

    @classmethod
    def default(cls) -> "RuntimeLayout":
        return cls.from_integration_root(INTEGRATION_ROOT)

    @classmethod
    def from_integration_root(cls, integration_root: Path) -> "RuntimeLayout":
        root = integration_root.expanduser().resolve()
        runtime = (root / ".runtime").resolve(strict=False)
        if runtime.parent != root:
            raise ConfigurationError(
                "Le runtime OpenCode doit être un enfant direct du plugin"
            )
        return cls(integration_root=root, runtime_root=runtime)

    @property
    def bin_dir(self) -> Path:
        return self.runtime_root / "bin"

    @property
    def config_dir(self) -> Path:
        return self.runtime_root / "config"

    @property
    def data_dir(self) -> Path:
        return self.runtime_root / "data"

    @property
    def cache_dir(self) -> Path:
        return self.runtime_root / "cache"

    @property
    def state_dir(self) -> Path:
        return self.runtime_root / "state"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_root / "logs"

    @property
    def tmp_dir(self) -> Path:
        return self.runtime_root / "tmp"

    @property
    def binary_path(self) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return self.bin_dir / f"opencode{suffix}"

    @property
    def manager_config_path(self) -> Path:
        return self.config_dir / "manager.json"

    @property
    def opencode_config_path(self) -> Path:
        return self.config_dir / "opencode.json"

    @property
    def install_state_path(self) -> Path:
        return self.state_dir / "install.json"

    @property
    def process_state_path(self) -> Path:
        return self.state_dir / "process.json"

    @property
    def auth_state_path(self) -> Path:
        return self.state_dir / "server-auth.json"

    def ensure(self) -> None:
        """Crée la frontière runtime avec des permissions privées."""

        for directory in (
            self.runtime_root,
            self.bin_dir,
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.state_dir,
            self.logs_dir,
            self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if _path_is_link_like(directory):
                raise ConfigurationError(
                    f"Lien ou point de réanalyse runtime interdit: {directory}"
                )
            if os.name != "nt":
                directory.chmod(0o700)


@dataclass(frozen=True, slots=True)
class OpenCodeSettings:
    hostname: str = "127.0.0.1"
    username: str = "jarvis-opencode"
    startup_timeout_seconds: float = 20.0
    shutdown_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 15.0
    sse_connect_timeout_seconds: float = 10.0
    sse_read_timeout_seconds: float = 90.0
    reconnect_base_seconds: float = 0.25
    reconnect_max_seconds: float = 5.0
    reconnect_jitter_seconds: float = 0.25
    reconnect_attempts: int = 6
    max_archive_bytes: int = 268_435_456
    max_extracted_bytes: int = 536_870_912

    def __post_init__(self) -> None:
        if self.hostname != "127.0.0.1":
            raise ConfigurationError(
                "OpenCode doit écouter exclusivement sur 127.0.0.1"
            )
        if not _USERNAME_RE.fullmatch(self.username):
            raise ConfigurationError("Username Basic Auth invalide")
        positive = (
            self.startup_timeout_seconds,
            self.shutdown_timeout_seconds,
            self.request_timeout_seconds,
            self.sse_connect_timeout_seconds,
            self.sse_read_timeout_seconds,
            self.reconnect_base_seconds,
            self.reconnect_max_seconds,
        )
        if any(value <= 0 for value in positive):
            raise ConfigurationError("Les délais doivent être strictement positifs")
        if self.reconnect_jitter_seconds < 0 or self.reconnect_attempts < 0:
            raise ConfigurationError("Configuration de reconnexion invalide")
        if self.max_archive_bytes <= 0 or self.max_extracted_bytes <= 0:
            raise ConfigurationError("Les limites d'archive doivent être positives")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "OpenCodeSettings":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ConfigurationError(
                f"Clés de configuration inconnues: {', '.join(unknown)}"
            )
        return cls(**dict(values))

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def _load_object(path: Path) -> dict[str, Any]:
    if _path_is_link_like(path):
        raise ConfigurationError(f"Configuration liée ou réanalysée interdite: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Configuration illisible: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"La configuration doit être un objet JSON: {path}")
    return value


def load_settings(layout: RuntimeLayout | None = None) -> OpenCodeSettings:
    layout = layout or RuntimeLayout.default()
    merged = _load_object(DEFAULTS_FILE)
    merged.update(_load_object(layout.manager_config_path))
    return OpenCodeSettings.from_mapping(merged)


def _atomic_write_json(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _path_is_link_like(path.parent) or _path_is_link_like(path):
        raise ConfigurationError(
            f"Écriture via lien ou point de réanalyse interdite: {path}"
        )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(mode)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def write_settings(
    settings: OpenCodeSettings, layout: RuntimeLayout | None = None
) -> Path:
    layout = layout or RuntimeLayout.default()
    layout.ensure()
    _atomic_write_json(layout.manager_config_path, settings.to_mapping())
    return layout.manager_config_path


def _validate_mcp_text(value: Any, *, label: str, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or len(value) > _MAX_MCP_ARGUMENT_LENGTH
    ):
        raise ConfigurationError(f"Valeur MCP invalide: {label}")
    if not allow_empty and not value:
        raise ConfigurationError(f"Valeur MCP vide interdite: {label}")
    return value


def _normalize_mcp_server(name: Any, value: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not _MCP_SERVER_NAME_RE.fullmatch(name):
        raise ConfigurationError(f"Nom de serveur MCP invalide: {name!r}")
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Le serveur MCP {name!r} doit être un objet")

    unknown = sorted((key for key in value if key not in _MCP_LOCAL_KEYS), key=str)
    if unknown:
        raise ConfigurationError(
            f"Clés MCP non autorisées pour {name!r}: {', '.join(str(item) for item in unknown)}"
        )
    if value.get("type") != "local":
        raise ConfigurationError(
            "Seuls les serveurs MCP locaux sont autorisés au runtime"
        )

    command = value.get("command")
    if not isinstance(command, (list, tuple)) or not command:
        raise ConfigurationError(f"Commande MCP absente pour {name!r}")
    normalized_command = [
        _validate_mcp_text(
            item, label=f"{name}.command[{index}]", allow_empty=index > 0
        )
        for index, item in enumerate(command)
    ]
    if not Path(normalized_command[0]).is_absolute():
        raise ConfigurationError(
            f"L'exécutable MCP doit être un chemin absolu: {name!r}"
        )

    normalized: dict[str, Any] = {"type": "local", "command": normalized_command}
    if "environment" in value:
        environment = value["environment"]
        if not isinstance(environment, Mapping):
            raise ConfigurationError(f"Environnement MCP invalide pour {name!r}")
        disallowed = sorted(
            (key for key in environment if key not in _MCP_ENVIRONMENT_KEYS), key=str
        )
        if disallowed:
            raise ConfigurationError(
                f"Variables MCP non autorisées pour {name!r}: {', '.join(str(item) for item in disallowed)}"
            )
        normalized_environment: dict[str, str] = {}
        for key, raw_value in environment.items():
            value_text = _validate_mcp_text(
                raw_value, label=f"{name}.environment.{key}"
            )
            paths = value_text.split(os.pathsep)
            if any(not item or not Path(item).is_absolute() for item in paths):
                raise ConfigurationError(
                    f"PYTHONPATH MCP doit contenir uniquement des chemins absolus: {name!r}"
                )
            normalized_environment[str(key)] = value_text
        normalized["environment"] = normalized_environment

    if "enabled" in value:
        enabled = value["enabled"]
        if not isinstance(enabled, bool):
            raise ConfigurationError(f"enabled MCP doit être booléen pour {name!r}")
        normalized["enabled"] = enabled
    if "timeout" in value:
        timeout = value["timeout"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not (1 <= timeout <= _MAX_MCP_TIMEOUT_MS)
        ):
            raise ConfigurationError(f"Timeout MCP invalide pour {name!r}")
        normalized["timeout"] = timeout
    return normalized


def normalize_runtime_config_overlay(
    overlay: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Valide l'overlay éphémère sans permettre d'affaiblir la configuration."""

    if overlay is None:
        return {}
    if not isinstance(overlay, Mapping):
        raise ConfigurationError("L'overlay runtime OpenCode doit être un objet")
    unknown = sorted(
        (key for key in overlay if key not in _RUNTIME_OVERLAY_KEYS), key=str
    )
    if unknown:
        raise ConfigurationError(
            "Clés d'overlay runtime interdites: "
            + ", ".join(str(item) for item in unknown)
        )
    if "mcp" not in overlay:
        return {}
    servers = overlay["mcp"]
    if not isinstance(servers, Mapping):
        raise ConfigurationError("La clé mcp de l'overlay runtime doit être un objet")
    return {
        "mcp": {
            str(name): _normalize_mcp_server(name, server)
            for name, server in servers.items()
        }
    }


def provision_runtime_config(
    layout: RuntimeLayout | None = None,
    *,
    runtime_config_overlay: Mapping[str, Any] | None = None,
) -> Path:
    """Copie la configuration OpenCode durcie dans le runtime confiné."""

    layout = layout or RuntimeLayout.default()
    layout.ensure()
    config = _load_object(OPENCODE_CONFIG_TEMPLATE)
    overlay = normalize_runtime_config_overlay(runtime_config_overlay)
    # Fusion par serveur : l'overlay runtime (broker JARVIS) ne doit pas
    # écraser d'autres MCP déjà déclarés dans le template (ex. jarvis-e2e).
    if "mcp" in overlay:
        existing = config.get("mcp")
        merged: dict[str, Any] = (
            dict(existing) if isinstance(existing, Mapping) else {}
        )
        merged.update(overlay["mcp"])
        config["mcp"] = merged
    _atomic_write_json(layout.opencode_config_path, config)
    return layout.opencode_config_path
