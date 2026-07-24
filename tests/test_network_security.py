"""Tests des garde-fous appliqués avant l'écoute réseau."""

from __future__ import annotations

import pytest

from core.network_security import is_loopback_host, validate_network_bind


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_local(host: str):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "jarvis.local"])
def test_network_hosts_are_not_loopback(host: str):
    assert is_loopback_host(host) is False


def test_local_http_bind_is_allowed_without_token():
    validate_network_bind(
        host="127.0.0.1",
        allow_network_bind=False,
        https_enabled=False,
        location_token="",
    )


def test_network_bind_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="WEB_ALLOW_NETWORK_BIND=true"):
        validate_network_bind(
            host="0.0.0.0",
            allow_network_bind=False,
            https_enabled=True,
            location_token="location-secret",
        )


def test_network_bind_refuses_unprotected_http():
    with pytest.raises(RuntimeError, match="WEB_HTTPS"):
        validate_network_bind(
            host="0.0.0.0",
            allow_network_bind=True,
            https_enabled=False,
            location_token="",
        )


@pytest.mark.parametrize(
    ("https_enabled", "location_token"),
    [(True, ""), (False, "location-secret")],
)
def test_network_bind_accepts_explicit_protected_configuration(
    https_enabled: bool,
    location_token: str,
):
    validate_network_bind(
        host="0.0.0.0",
        allow_network_bind=True,
        https_enabled=https_enabled,
        location_token=location_token,
    )
