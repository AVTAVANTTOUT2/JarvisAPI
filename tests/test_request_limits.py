"""Plafonds Content-Length : matrice pure hors stack HTTP."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from api.request_limits import content_length_error, request_size_limit


def test_request_size_limit_maps_capped_routes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 4096, raising=False)
    monkeypatch.setattr(config, "RECORDING_CHUNK_SIZE_MB", 2, raising=False)
    monkeypatch.setattr(config, "MOBILE_VOICE_MAX_REQUEST_BYTES", 1024, raising=False)
    monkeypatch.setattr(config, "REMOTE_SCREEN_MAX_REQUEST_BYTES", 2048, raising=False)

    assert request_size_limit("POST", "/api/agentic/runs") == 4096
    assert request_size_limit("DELETE", "/api/agentic/runs/abc") == 4096
    assert request_size_limit(
        "PUT", "/api/recording-sessions/sess-1/chunks/0"
    ) == 2 * 1024 * 1024
    assert request_size_limit("POST", "/api/mobile/voice/turn") == 1024
    assert request_size_limit("POST", "/api/devices/mac-1/screen") == 2048


def test_request_size_limit_excludes_uncapped_routes():
    assert request_size_limit("GET", "/api/agentic/runs") is None
    assert request_size_limit("POST", "/api/tasks") is None
    assert request_size_limit("PUT", "/api/recording-sessions/sess-1") is None
    assert request_size_limit("POST", "/api/devices/mac-1/heartbeat") is None
    # séquence non numérique : hors contrat chunk
    assert request_size_limit("PUT", "/api/recording-sessions/sess-1/chunks/x") is None


def _request(method: str, path: str, content_length: str | None) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if content_length is not None:
        headers["content-length"] = content_length
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=headers,
    )


def test_content_length_error_requires_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 100, raising=False)
    response = content_length_error(_request("POST", "/api/agentic/runs", None))
    assert response is not None
    assert response.status_code == 411
    assert response.body  # JSONResponse materialised


@pytest.mark.parametrize("raw_length", ["abc", "-1", "1.5"])
def test_content_length_error_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, raw_length: str
):
    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 100, raising=False)
    response = content_length_error(
        _request("POST", "/api/agentic/runs", raw_length)
    )
    assert response is not None
    assert response.status_code == 400


def test_content_length_error_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 100, raising=False)
    response = content_length_error(_request("POST", "/api/agentic/runs", "101"))
    assert response is not None
    assert response.status_code == 413


def test_content_length_error_allows_in_limit_and_uncapped(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(config, "AGENTIC_MAX_REQUEST_BYTES", 100, raising=False)
    assert (
        content_length_error(_request("POST", "/api/agentic/runs", "100")) is None
    )
    assert content_length_error(_request("POST", "/api/tasks", None)) is None
