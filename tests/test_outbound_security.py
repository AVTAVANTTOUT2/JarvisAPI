"""Contrats SSRF des destinations HTTP sortantes."""

from __future__ import annotations

import socket

import pytest

from core.outbound_security import (
    OutboundURLRejected,
    validate_open_world_https_url,
    validate_public_https_url,
)


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


def _any_host_resolver(*addresses: str):
    def resolve(host: str, port: int, *, type: int):
        assert port == 443
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET, type, 6, "", (address, port)) for address in addresses
        ]

    return resolve


def test_open_world_https_accepts_any_public_host():
    endpoint = "https://hotels.example.com/search?city=Barcelona"
    assert (
        validate_open_world_https_url(
            endpoint,
            resolver=_any_host_resolver("8.8.8.8"),
        )
        == endpoint
    )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://HOTELS.EXAMPLE.COM", "https://hotels.example.com/"),
        ("https://hotels.example.com:443", "https://hotels.example.com/"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
    ],
)
def test_open_world_https_canonicalizes_the_exact_browser_destination(
    endpoint: str, expected: str
):
    assert (
        validate_open_world_https_url(
            endpoint,
            resolver=_any_host_resolver("8.8.8.8"),
        )
        == expected
    )


def test_open_world_https_still_rejects_private_resolution():
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            "https://hotels.example/search",
            resolver=_any_host_resolver("10.0.0.2"),
        )
    assert caught.value.code == "non_public_address"


def test_open_world_https_rejects_mixed_public_and_private_resolution():
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            "https://hotels.example.com/search",
            resolver=_any_host_resolver("8.8.8.8", "10.0.0.2"),
        )
    assert caught.value.code == "non_public_address"


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "data:text/plain,secret",
        "javascript:alert(1)",
        "blob:https://public.example.com/id",
        "ftp://public.example.com/file",
        "https://user@public.example.com/",
    ],
)
def test_open_world_rejects_non_https_and_userinfo(endpoint: str):
    with pytest.raises(OutboundURLRejected):
        validate_open_world_https_url(
            endpoint,
            resolver=_any_host_resolver("8.8.8.8"),
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://2130706433/",
        "https://0177.0.0.1/",
        "https://0x7f000001/",
        "https://127.1/",
    ],
)
def test_open_world_rejects_ambiguous_ip_representations(endpoint: str):
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            endpoint,
            resolver=_any_host_resolver("8.8.8.8"),
        )
    assert caught.value.code == "ambiguous_ip_address"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://printer/",
        "https://service.local/",
        "https://metadata.google.internal/",
        "https://instance-data.ec2.internal/",
    ],
)
def test_open_world_rejects_local_and_metadata_names(endpoint: str):
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            endpoint,
            resolver=_any_host_resolver("8.8.8.8"),
        )
    assert caught.value.code == "local_hostname"


def test_open_world_rejects_ipv4_mapped_ipv6_even_when_public():
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            "https://public.example.com/",
            resolver=_any_host_resolver("::ffff:8.8.8.8"),
        )
    assert caught.value.code == "ambiguous_ip_address"


@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "0.0.0.0",
    ],
)
def test_open_world_rejects_multicast_reserved_and_unspecified(address: str):
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            "https://public.example.com/",
            resolver=_any_host_resolver(address),
        )
    assert caught.value.code == "non_public_address"


@pytest.mark.parametrize(
    "address",
    [
        "::127.0.0.1",
        "64:ff9b::7f00:1",
        "64:ff9b:1::7f00:1",
        "2002:7f00:1::",
    ],
)
def test_open_world_rejects_ipv4_encapsulation_in_ipv6(address: str):
    with pytest.raises(OutboundURLRejected) as caught:
        validate_open_world_https_url(
            "https://public.example.com/",
            resolver=_any_host_resolver(address),
        )
    assert caught.value.code == "ambiguous_ip_address"
