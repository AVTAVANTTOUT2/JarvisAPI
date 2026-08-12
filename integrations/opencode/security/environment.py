"""Construction explicite de l'environnement enfant OpenCode."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Collection, Mapping

from integrations.opencode.config import RuntimeLayout, provision_runtime_config

from .paths import is_link_like


class EnvironmentSecurityError(ValueError):
    """Une variable non autorisée ou mal formée a été demandée."""


BASE_ALLOWLIST = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
)

_MANAGED_KEYS = frozenset(
    {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_SERVER_USERNAME",
        "OPENCODE_SERVER_PASSWORD",
        "NO_COLOR",
    }
)


def _valid_value(key: str, value: str) -> str:
    if not key or "=" in key or "\x00" in key or "\x00" in value:
        raise EnvironmentSecurityError(f"Variable d'environnement invalide: {key!r}")
    return value


def build_child_environment(
    layout: RuntimeLayout,
    *,
    username: str,
    password: str,
    source: Mapping[str, str] | None = None,
    explicit: Mapping[str, str] | None = None,
    additional_allowlist: Collection[str] = (),
    runtime_config_overlay: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Construit un environnement minimal, sans héritage implicite de secrets."""

    layout.ensure()
    config_file = provision_runtime_config(
        layout, runtime_config_overlay=runtime_config_overlay
    )
    inherited = source if source is not None else os.environ
    allowed = BASE_ALLOWLIST | frozenset(additional_allowlist)
    result = {
        key: _valid_value(key, value)
        for key, value in inherited.items()
        if key in allowed
    }

    explicit = explicit or {}
    for key, value in explicit.items():
        if key not in allowed or key in _MANAGED_KEYS:
            raise EnvironmentSecurityError(f"Variable explicite non autorisée: {key}")
        result[key] = _valid_value(key, value)

    home = layout.data_dir / "home"
    xdg_config = layout.config_dir / "xdg"
    xdg_data = layout.data_dir / "xdg"
    xdg_cache = layout.cache_dir / "xdg"
    xdg_state = layout.state_dir / "xdg"
    for directory in (home, xdg_config, xdg_data, xdg_cache, xdg_state, layout.tmp_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if is_link_like(directory):
            raise EnvironmentSecurityError(
                f"Lien ou point de réanalyse interdit: {directory}"
            )
        if os.name != "nt":
            directory.chmod(0o700)

    result.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_STATE_HOME": str(xdg_state),
            "TMPDIR": str(layout.tmp_dir),
            "TEMP": str(layout.tmp_dir),
            "TMP": str(layout.tmp_dir),
            "OPENCODE_CONFIG": str(config_file),
            "OPENCODE_CONFIG_DIR": str(layout.config_dir),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_SERVER_USERNAME": _valid_value(
                "OPENCODE_SERVER_USERNAME", username
            ),
            "OPENCODE_SERVER_PASSWORD": _valid_value(
                "OPENCODE_SERVER_PASSWORD", password
            ),
            "NO_COLOR": "1",
        }
    )
    return result


def environment_paths(environment: Mapping[str, str]) -> tuple[Path, ...]:
    """Retourne les chemins gérés, utile pour les vérifications de confinement."""

    keys = (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
    )
    return tuple(Path(environment[key]) for key in keys)
