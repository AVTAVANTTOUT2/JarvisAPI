"""Health-check authentifié et versionné du serveur OpenCode local."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from integrations.opencode.security.paths import validate_loopback_url


@dataclass(frozen=True, slots=True)
class HealthReport:
    healthy: bool
    version: str | None
    status_code: int | None
    error_code: str | None = None


def check_health(
    base_url: str,
    *,
    username: str,
    password: str,
    expected_version: str,
    timeout_seconds: float = 2.0,
) -> HealthReport:
    """Interroge ``/global/health`` sans proxy ni fuite du secret."""

    origin = validate_loopback_url(base_url)
    try:
        with httpx.Client(
            base_url=origin,
            auth=httpx.BasicAuth(username, password),
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        ) as client:
            response = client.get("/global/health")
    except httpx.TimeoutException:
        return HealthReport(False, None, None, "timeout")
    except httpx.RequestError:
        return HealthReport(False, None, None, "network")
    if response.status_code == 401:
        return HealthReport(False, None, 401, "authentication")
    if response.status_code != 200:
        return HealthReport(False, None, response.status_code, "protocol")
    try:
        payload: Any = response.json()
    except ValueError:
        return HealthReport(False, None, 200, "invalid_json")
    if not isinstance(payload, dict) or payload.get("healthy") is not True:
        return HealthReport(False, None, 200, "invalid_schema")
    version = payload.get("version")
    if not isinstance(version, str):
        return HealthReport(False, None, 200, "invalid_schema")
    if version != expected_version:
        return HealthReport(False, version, 200, "version_mismatch")
    return HealthReport(True, version, 200)
