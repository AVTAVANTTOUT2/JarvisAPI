"""Dernière ligne DeepSeek : PII intactes, secrets masqués, pas de DataLeakError."""

from __future__ import annotations

import pytest

from jarvis.backends.deepseek import DeepSeekBackend
from jarvis.exceptions import DataLeakError
from jarvis.pii.boundary import DataBoundary

_EMAIL = "marie.martin@gmail.com"
_SECRET = "sk-passThroughSecret123456789"


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}]}


class _CapturingClient:
    def __init__(self) -> None:
        self.payload: dict | None = None

    async def post(self, _path: str, json: dict | None = None, headers=None):
        self.payload = json
        return _FakeResponse()


@pytest.fixture()
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "sk-test-deepseek-boundary-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    return key


async def test_generate_keeps_email_and_redacts_secret(api_key: str) -> None:
    client = _CapturingClient()
    backend = DeepSeekBackend(boundary=DataBoundary(), client=client)
    await backend.generate(prompt=f"Écris à {_EMAIL} avec {_SECRET}")

    assert client.payload is not None
    user_content = client.payload["messages"][-1]["content"]
    assert _EMAIL in user_content
    assert _SECRET not in user_content
    assert api_key not in user_content


async def test_generate_does_not_raise_data_leak_on_pii(api_key: str) -> None:
    client = _CapturingClient()
    backend = DeepSeekBackend(boundary=DataBoundary(), client=client)
    try:
        await backend.generate(prompt=f"Réponds à {_EMAIL}")
    except DataLeakError as exc:
        pytest.fail(f"un e-mail n'est plus une fuite DataBoundary : {exc}")
    assert client.payload is not None
    assert _EMAIL in client.payload["messages"][-1]["content"]
