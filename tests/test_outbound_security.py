"""Contrats SSRF des destinations HTTP sortantes."""

from __future__ import annotations

import socket

import pytest

from core.outbound_security import OutboundURLRejected, validate_public_https_url


def _resolver(*addresses: str):
    def resolve(host: str, port: int, *, type: int):
        assert host == "fcm.googleapis.com"
        assert port == 443
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, type, 6, "", (address, port))
            for address in addresses
        ]

    return resolve


def test_public_allowlisted_https_destination_is_accepted():
    endpoint = "https://fcm.googleapis.com/fcm/send/abc"
    assert validate_public_https_url(
        endpoint,
        allowed_hosts="fcm.googleapis.com",
        resolver=_resolver("8.8.8.8", "2606:4700:4700::1111"),
    ) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://user:pass@fcm.googleapis.com/fcm/send/abc",
        "https://fcm.googleapis.com:8443/fcm/send/abc",
        "https://fcm.googleapis.com/fcm/send/abc#fragment",
        "https://evil.example/fcm/send/abc",
    ],
)
def test_destination_contract_rejects_invalid_url_or_host(endpoint):
    with pytest.raises(OutboundURLRejected):
        validate_public_https_url(
            endpoint,
            allowed_hosts="fcm.googleapis.com",
            resolver=_resolver("8.8.8.8"),
        )


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.1.2", "::1"])
def test_destination_rejects_private_loopback_and_link_local_resolution(address):
    with pytest.raises(OutboundURLRejected) as caught:
        validate_public_https_url(
            "https://fcm.googleapis.com/fcm/send/abc",
            allowed_hosts="fcm.googleapis.com",
            resolver=_resolver(address),
        )
    assert caught.value.code == "non_public_address"
