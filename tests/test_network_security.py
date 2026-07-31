"""Tests des garde-fous appliqués avant l'écoute réseau."""

from __future__ import annotations

import pytest

from core.network_security import (
    is_loopback_host,
    validate_network_bind,
    validate_supervisor_network_bind,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_local(host: str):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "jarvis.local"])
def test_network_hosts_are_not_loopback(host: str):
    assert is_loopback_host(host) is False


def test_local_http_bind_is_allowed():
    validate_network_bind(
        host="127.0.0.1",
        allow_network_bind=False,
        https_enabled=False,
    )


def test_local_reverse_proxy_tls_mode_is_allowed():
    validate_network_bind(
        host="127.0.0.1",
        allow_network_bind=False,
        https_enabled=False,
        https_behind_proxy=True,
    )


def test_direct_and_proxy_tls_modes_are_mutually_exclusive():
    with pytest.raises(RuntimeError, match="mutuellement exclusifs"):
        validate_network_bind(
            host="127.0.0.1",
            allow_network_bind=False,
            https_enabled=True,
            https_behind_proxy=True,
        )


def test_network_bind_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="WEB_ALLOW_NETWORK_BIND=true"):
        validate_network_bind(
            host="0.0.0.0",
            allow_network_bind=False,
            https_enabled=True,
        )


def test_network_bind_refuses_http_unconditionally():
    with pytest.raises(RuntimeError, match="écoute HTTP refusée"):
        validate_network_bind(
            host="0.0.0.0",
            allow_network_bind=True,
            https_enabled=False,
        )


def test_network_bind_refuses_proxy_mode_on_network_interface():
    with pytest.raises(RuntimeError, match="WEB_HOST loopback"):
        validate_network_bind(
            host="0.0.0.0",
            allow_network_bind=True,
            https_enabled=False,
            https_behind_proxy=True,
        )


def test_network_bind_accepts_explicit_direct_https():
    validate_network_bind(
        host="0.0.0.0",
        allow_network_bind=True,
        https_enabled=True,
    )


def test_supervisor_network_bind_requires_configured_auth():
    with pytest.raises(RuntimeError, match="configurez d'abord"):
        validate_supervisor_network_bind(
            host="0.0.0.0",
            allow_network_bind=True,
            https_enabled=True,
            auth_configured=False,
        )


def test_supervisor_network_bind_accepts_https_with_configured_auth():
    validate_supervisor_network_bind(
        host="0.0.0.0",
        allow_network_bind=True,
        https_enabled=True,
        auth_configured=True,
    )
