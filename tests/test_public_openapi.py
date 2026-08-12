"""Contrat public OpenAPI et documentation développeur sécurisée."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi.openapi.models import OpenAPI

from tests.conftest import authenticate

ROOT = Path(__file__).resolve().parents[1]


def _operations(schema: dict):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                yield path, method, operation


def test_public_contract_is_complete_stable_and_tagged() -> None:
    import main

    schema = main.app.openapi()
    OpenAPI.model_validate(schema)
    operations = list(_operations(schema))
    operation_ids = [operation["operationId"] for _, _, operation in operations]

    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["title"] == "JARVIS Developer API"
    assert schema["info"]["version"] == "1.0.0"
    assert schema["x-jarvis-contract-version"] == "1.0.0"
    assert schema["x-jarvis-docs-path"] == "/api/developer/docs"
    assert len(operation_ids) == len(set(operation_ids))
    assert all(operation_id == operation_id.lower() for operation_id in operation_ids)
    assert all(operation.get("tags") for _, _, operation in operations)
    assert all(operation.get("description") for _, _, operation in operations)
    assert all("security" in operation for _, _, operation in operations)

    schemes = schema["components"]["securitySchemes"]
    assert set(schemes) == {
        "sessionCookie",
        "csrfToken",
        "mobileBearer",
        "deviceToken",
        "locationToken",
        "visualReadBearer",
    }


def test_public_contract_documents_runtime_auth_boundaries() -> None:
    import main

    paths = main.app.openapi()["paths"]
    assert paths["/api/health/live"]["get"]["security"] == []
    assert paths["/api/auth/status"]["get"]["security"] == []
    assert paths["/api/tasks"]["get"]["security"] == [
        {"sessionCookie": []},
        {"mobileBearer": []},
    ]
    assert paths["/api/tasks"]["post"]["security"] == [
        {"sessionCookie": [], "csrfToken": []}
    ]
    assert paths["/api/backups/run"]["post"]["x-jarvis-csrf-origin-required"] is True
    assert paths["/api/devices/{device_id}/heartbeat"]["post"]["security"] == [
        {"deviceToken": []}
    ]
    assert paths["/api/location"]["post"]["security"] == [
        {"mobileBearer": []},
        {"locationToken": []},
    ]
    assert paths["/api/mobile/pairing/complete"]["post"][
        "x-jarvis-authentication"
    ] == "pairing_code"
    assert paths["/api/mobile/chat"]["post"]["security"] == [{"mobileBearer": []}]


def test_operation_ids_do_not_depend_on_handler_names() -> None:
    from api.openapi import operation_id_for

    assert operation_id_for("GET", "/api/conversations/{conversation_id}") == (
        "get_api_conversations_by_conversation_id"
    )
    assert operation_id_for("POST", "/api/backups/cloud/{name}/restore") == (
        "post_api_backups_cloud_by_name_restore"
    )


def test_developer_docs_remain_session_protected_and_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    import database
    import main

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    with TestClient(main.app) as client:
        assert client.get("/api/developer/openapi.json").status_code in {401, 428}
        assert client.get("/api/developer/docs").status_code in {401, 428}
        authenticate(client)

        schema_response = client.get("/api/developer/openapi.json")
        assert schema_response.status_code == 200
        assert schema_response.headers["cache-control"] == "no-store"
        assert schema_response.json()["info"]["version"] == "1.0.0"

        docs_response = client.get("/api/developer/docs")
        assert docs_response.status_code == 200
        assert docs_response.headers["cache-control"] == "no-store"
        assert "style-src 'self' 'sha256-" in docs_response.headers[
            "content-security-policy"
        ]
        assert "cdn" not in docs_response.text.lower()
        assert "get_api_tasks" in docs_response.text
        assert "./openapi.json" in docs_response.text


def test_committed_openapi_artifact_is_current() -> None:
    from tools.export_openapi import (
        DEFAULT_OUTPUT,
        contract_is_current,
        stale_contract_message,
    )

    assert DEFAULT_OUTPUT == ROOT / "openapi" / "jarvis.openapi.json"
    assert contract_is_current(DEFAULT_OUTPUT), stale_contract_message(DEFAULT_OUTPUT)


def test_openapi_artifact_is_valid_json() -> None:
    artifact = ROOT / "openapi" / "jarvis.openapi.json"
    schema = json.loads(artifact.read_text(encoding="utf-8"))
    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["version"] == "1.0.0"
