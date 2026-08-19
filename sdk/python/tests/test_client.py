from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from jarvis_sdk import (
    CONTRACT_VERSION,
    OPERATIONS,
    JarvisApiError,
    JarvisAuthenticationError,
    JarvisClient,
    JarvisConfigurationError,
    JarvisResponseTooLarge,
    JarvisTransportError,
    __version__,
)
from jarvis_sdk.client import _NoRedirectHandler

ROOT = Path(__file__).resolve().parents[3]


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._stream = io.BytesIO(body)
        self.headers = headers or {"Content-Type": "application/json"}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.requests: list[tuple[Any, float]] = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def http_error(status: int, payload: dict[str, Any], **headers: str) -> HTTPError:
    message = Message()
    message["Content-Type"] = "application/json"
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return HTTPError(
        "https://jarvis.test/api",
        status,
        "error",
        message,
        io.BytesIO(json.dumps(payload).encode()),
    )


def test_registry_matches_canonical_openapi() -> None:
    schema = json.loads((ROOT / "openapi" / "jarvis.openapi.json").read_text())
    expected = {}
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                if method == "get" and path.startswith("/api/visual/v1/"):
                    continue
                expected[operation["operationId"]] = (
                    method.upper(),
                    path,
                    operation["x-jarvis-authentication"],
                )
    assert len(OPERATIONS) == 315
    assert set(OPERATIONS) == set(expected)
    assert all(
        (operation.method, operation.path, operation.auth) == expected[operation_id]
        for operation_id, operation in OPERATIONS.items()
    )
    assert CONTRACT_VERSION == __version__ == schema["info"]["version"] == "1.0.0"
    assert {operation.auth for operation in OPERATIONS.values()} == {
        "device_token",
        "mobile_bearer",
        "mobile_or_location_token",
        "pairing_code",
        "public",
        "session",
        "session_or_mobile",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://jarvis.example.test:8080",
        "ftp://127.0.0.1",
        "https://user:secret@jarvis.test",
        "https://jarvis.test?token=secret",
        "https://jarvis.test/#fragment",
    ],
)
def test_base_url_rejects_unsafe_forms(url: str) -> None:
    with pytest.raises(JarvisConfigurationError):
        JarvisClient(url, _opener=FakeOpener())


def test_bearer_request_encodes_path_query_and_profile() -> None:
    opener = FakeOpener(FakeResponse(200, b'{"ok":true}'))
    client = JarvisClient(
        "https://jarvis.test/root/",
        bearer_token="mobile-secret",
        profile_id="alice",
        _opener=opener,
    )
    payload = client.call_json(
        "get_api_conversations_by_conv_id",
        path_params={"conv_id": "42/notes"},
        query={"include": ["messages", "summary"], "skip": None},
    )

    request, timeout = opener.requests[0]
    assert payload == {"ok": True}
    assert request.full_url == (
        "https://jarvis.test/root/api/conversations/42%2Fnotes"
        "?include=messages&include=summary"
    )
    assert request.get_header("Authorization") == "Bearer mobile-secret"
    assert request.get_header("X-jarvis-profile") == "alice"
    assert timeout == 30.0


def test_path_parameters_must_match_exactly() -> None:
    client = JarvisClient("http://127.0.0.1:8080", _opener=FakeOpener())
    with pytest.raises(JarvisConfigurationError, match="manquants"):
        client.call("get_api_conversations_by_conv_id")
    with pytest.raises(JarvisConfigurationError, match="en trop"):
        client.call(
            "get_api_health_live",
            path_params={"unexpected": 1},
        )


def test_session_mutation_sends_cookie_csrf_origin_and_json() -> None:
    opener = FakeOpener(FakeResponse(201, b'{"id":7}'))
    client = JarvisClient(
        "http://localhost:8080",
        session_token="session-secret",
        csrf_token="csrf-secret",
        _opener=opener,
    )
    result = client.call_json("post_api_tasks", json_body={"title": "Test"})

    request, _ = opener.requests[0]
    assert result == {"id": 7}
    assert request.get_header("Cookie") == "jarvis_session=session-secret"
    assert request.get_header("X-csrf-token") == "csrf-secret"
    assert request.get_header("Origin") == "http://localhost:8080"
    assert request.get_header("Content-type") == "application/json"
    assert request.data == b'{"title":"Test"}'


def test_session_and_mobile_credentials_fail_closed() -> None:
    client = JarvisClient("https://jarvis.test", _opener=FakeOpener())
    with pytest.raises(JarvisAuthenticationError, match="session"):
        client.call("get_api_backups")
    with pytest.raises(JarvisAuthenticationError, match="bearer_token"):
        client.call("post_api_mobile_chat", json_body={"message": "Bonjour"})
    with pytest.raises(JarvisAuthenticationError, match="device_token"):
        client.call(
            "post_api_devices_by_device_id_heartbeat",
            path_params={"device_id": "tv"},
            json_body={},
        )


def test_device_and_location_tokens_use_their_dedicated_headers() -> None:
    opener = FakeOpener(FakeResponse(200, b"{}"), FakeResponse(200, b"{}"))
    client = JarvisClient(
        "https://jarvis.test",
        device_token="device-secret",
        location_token="location-secret",
        _opener=opener,
    )
    client.call(
        "post_api_devices_by_device_id_heartbeat",
        path_params={"device_id": "tv"},
        json_body={},
    )
    client.call("post_api_location", json_body={"lat": 48.8, "lon": 2.3})
    assert opener.requests[0][0].get_header("X-device-token") == "device-secret"
    assert opener.requests[1][0].get_header("X-location-token") == "location-secret"
    assert opener.requests[1][0].get_header("X-csrf-token") is None


def test_unlock_enables_cookie_session_without_retaining_secret() -> None:
    opener = FakeOpener(
        FakeResponse(
            200,
            b'{"ok":true,"csrf_token":"csrf-from-server"}',
            {"Set-Cookie": "jarvis_session=opaque; HttpOnly; Path=/"},
        ),
        FakeResponse(200, b"[]"),
    )
    client = JarvisClient("https://jarvis.test", _opener=opener)

    assert client.unlock("correct horse battery staple")["ok"] is True
    assert client.call_json("get_api_backups") == []
    assert not hasattr(client, "_secret")
    assert opener.requests[1][0].get_header("Authorization") is None


def test_unlock_rejects_a_missing_session_cookie() -> None:
    client = JarvisClient(
        "https://jarvis.test",
        _opener=FakeOpener(
            FakeResponse(200, b'{"csrf_token":"csrf"}', {"Set-Cookie": "other=1"})
        ),
    )
    with pytest.raises(JarvisTransportError, match="unlock incomplète"):
        client.unlock("secret")
    with pytest.raises(JarvisAuthenticationError):
        client.call("get_api_backups")


def test_api_errors_are_typed_and_do_not_echo_credentials() -> None:
    opener = FakeOpener(
        http_error(400, {"detail": {"code": "invalid_task", "message": "Titre requis"}})
    )
    client = JarvisClient(
        "https://jarvis.test",
        session_token="top-secret-session",
        csrf_token="top-secret-csrf",
        _opener=opener,
    )
    with pytest.raises(JarvisApiError) as captured:
        client.call("post_api_tasks", json_body={})
    assert captured.value.status_code == 400
    assert captured.value.code == "invalid_task"
    assert "top-secret" not in str(captured.value)


def test_get_retries_transient_http_and_network_failures() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(
        URLError("temporary"),
        http_error(503, {"error": "busy"}, Retry_After="0"),
        FakeResponse(200, b'{"status":"ok"}'),
    )
    client = JarvisClient(
        "https://jarvis.test",
        retry_attempts=3,
        retry_delay_seconds=0,
        _opener=opener,
        _sleep=sleeps.append,
    )
    assert client.health() == {"status": "ok"}
    assert len(opener.requests) == 3
    assert sleeps == [0.0, 0.0]


def test_mutations_are_never_retried() -> None:
    opener = FakeOpener(http_error(503, {"error": "busy"}), FakeResponse(200, b"{}"))
    client = JarvisClient(
        "https://jarvis.test",
        session_token="session",
        csrf_token="csrf",
        retry_attempts=3,
        _opener=opener,
    )
    with pytest.raises(JarvisApiError) as captured:
        client.call("post_api_tasks", json_body={"title": "once"})
    assert captured.value.status_code == 503
    assert len(opener.requests) == 1


def test_response_size_is_bounded() -> None:
    opener = FakeOpener(FakeResponse(200, b"12345"))
    client = JarvisClient(
        "https://jarvis.test",
        max_response_bytes=4,
        _opener=opener,
    )
    with pytest.raises(JarvisResponseTooLarge):
        client.health()


def test_transport_errors_are_typed() -> None:
    client = JarvisClient(
        "https://jarvis.test",
        retry_attempts=1,
        _opener=FakeOpener(URLError("offline")),
    )
    with pytest.raises(JarvisTransportError, match="URLError"):
        client.health()


def test_reserved_and_invalid_headers_are_rejected() -> None:
    client = JarvisClient("https://jarvis.test", _opener=FakeOpener())
    with pytest.raises(JarvisConfigurationError, match="réservé"):
        client.call("get_api_health_live", headers={"Authorization": "Bearer bypass"})
    with pytest.raises(JarvisConfigurationError, match="invalide"):
        client.call("get_api_health_live", headers={"X-Test": "bad\r\nvalue"})


def test_missing_private_ca_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(JarvisConfigurationError, match="cafile"):
        JarvisClient("https://jarvis.test", cafile=tmp_path / "missing.pem")


def test_default_transport_never_follows_redirects() -> None:
    client = JarvisClient("https://jarvis.test")
    assert any(isinstance(handler, _NoRedirectHandler) for handler in client._opener.handlers)
    assert _NoRedirectHandler().redirect_request(None) is None
