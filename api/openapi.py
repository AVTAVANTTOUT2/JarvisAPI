"""Contrat OpenAPI public, déterministe et aligné sur la sécurité runtime."""

from __future__ import annotations

import re
from collections import defaultdict
from html import escape
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

import config
from api.middleware import _bypasses_session_gate, _mobile_bearer_allows

PUBLIC_API_CONTRACT_VERSION = "1.0.0"
OPENAPI_VERSION = "3.1.0"
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PAIRING_PATHS = frozenset(
    {
        ("POST", "/api/devices/register"),
        ("POST", "/api/mobile/pairing/complete"),
    }
)
_DEVICE_TOKEN_PATH_RE = re.compile(
    r"^/api/devices/\{[^}]+\}/(?:heartbeat|screen|tts)$"
)
_TAG_ALIASES = {
    "auth": "authentication",
    "backups": "backups",
    "conversations": "conversations",
    "devices": "devices",
    "health": "health",
    "mobile": "mobile",
    "stats": "observability",
    "metrics": "observability",
}
_TAG_DESCRIPTIONS = {
    "authentication": "Configuration, sessions navigateur, profils et pairage mobile.",
    "backups": "Sauvegardes locales et réplication cloud chiffrée.",
    "cognitive": "Mémoire, raisonnement, apprentissage et graphe cognitif.",
    "conversations": "Conversations, messages, recherche et actions associées.",
    "devices": "Pairage, présence et commandes des appareils JARVIS.",
    "fitness": "Entraînements, nutrition, bien-être et progression.",
    "health": "Sondes de vie et diagnostics de santé de l'instance.",
    "mobile": "Flux natifs mobiles authentifiés par jeton Bearer.",
    "observability": "Statistiques, métriques, coûts et historique opérationnel.",
}


def operation_id_for(method: str, path: str) -> str:
    """Construit un identifiant SDK stable à partir du contrat HTTP."""
    templated = re.sub(r"\{([^}:]+)(?::[^}]+)?\}", r"by_\1", path)
    suffix = re.sub(r"[^a-zA-Z0-9]+", "_", templated).strip("_").lower()
    return f"{method.lower()}_{suffix}"


def stable_operation_id(route: APIRoute) -> str:
    """Adaptateur FastAPI utilisé dès l'enregistrement des routes."""
    methods = sorted(method for method in (route.methods or ()) if method != "HEAD")
    method = methods[0] if methods else "GET"
    return operation_id_for(method, route.path_format)


def _example_runtime_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def _tag_for_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    domain = parts[1] if len(parts) > 1 and parts[0] == "api" else (parts[0] if parts else "root")
    return _TAG_ALIASES.get(domain, domain.replace("_", "-"))


def _security_for(method: str, path: str) -> tuple[list[dict[str, list]], str]:
    upper_method = method.upper()
    if upper_method == "GET" and path.startswith("/api/visual/v1/"):
        return [{"visualReadBearer": []}], "visual_read_bearer"
    if (upper_method, path) in _PAIRING_PATHS:
        return [], "pairing_code"
    if _DEVICE_TOKEN_PATH_RE.fullmatch(path):
        return [{"deviceToken": []}], "device_token"
    if upper_method == "POST" and path in {"/api/location", "/api/location/batch"}:
        return [{"mobileBearer": []}, {"locationToken": []}], "mobile_or_location_token"

    runtime_path = _example_runtime_path(path)
    if _bypasses_session_gate(upper_method, runtime_path):
        if path.startswith("/api/mobile/") and path != "/api/mobile/pairing/complete":
            return [{"mobileBearer": []}], "mobile_bearer"
        return [], "public"

    session: dict[str, list] = {"sessionCookie": []}
    if upper_method in _UNSAFE_METHODS:
        session["csrfToken"] = []
    alternatives = [session]
    if _mobile_bearer_allows(upper_method, runtime_path):
        alternatives.append({"mobileBearer": []})
    return alternatives, "session_or_mobile" if len(alternatives) > 1 else "session"


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiError"},
            }
        },
    }


def _document_operation(path: str, method: str, operation: dict[str, Any]) -> None:
    operation["operationId"] = operation_id_for(method, path)
    operation.setdefault("tags", [_tag_for_path(path)])
    operation.setdefault(
        "description",
        f"Opération JARVIS `{method.upper()} {path}`.",
    )
    security, auth_kind = _security_for(method, path)
    operation["security"] = security
    operation["x-jarvis-authentication"] = auth_kind
    if method.upper() in _UNSAFE_METHODS and any("csrfToken" in item for item in security):
        operation["x-jarvis-csrf-origin-required"] = True

    responses = operation.setdefault("responses", {})
    if security or auth_kind in {"pairing_code", "mobile_bearer", "device_token"}:
        responses.setdefault("401", {"$ref": "#/components/responses/Unauthorized"})
    if any("sessionCookie" in item for item in security):
        responses.setdefault("428", {"$ref": "#/components/responses/SetupRequired"})
    if any("csrfToken" in item for item in security):
        responses.setdefault("403", {"$ref": "#/components/responses/Forbidden"})
    if auth_kind == "pairing_code":
        responses.setdefault("429", {"$ref": "#/components/responses/RateLimited"})


def build_openapi_schema(app: FastAPI) -> dict[str, Any]:
    """Génère le contrat public complet puis l'enrichit sans I/O."""
    schema = get_openapi(
        title="JARVIS Developer API",
        version=PUBLIC_API_CONTRACT_VERSION,
        openapi_version=OPENAPI_VERSION,
        summary="API locale sécurisée de l'assistant JARVIS",
        description=(
            "Contrat développeur de l'instance JARVIS. Les routes restent protégées "
            "par la session locale, le CSRF ou les jetons mobiles/appareils décrits "
            "sur chaque opération."
        ),
        routes=app.routes,
        servers=[{"url": "/", "description": "Instance JARVIS courante"}],
    )
    schema["x-jarvis-contract-version"] = PUBLIC_API_CONTRACT_VERSION
    schema["x-jarvis-docs-path"] = "/api/developer/docs"
    components = schema.setdefault("components", {})
    components.setdefault("schemas", {})["ApiError"] = {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "detail": {},
        },
        "additionalProperties": True,
    }
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.update({
        "sessionCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": config.SESSION_COOKIE_NAME,
            "description": "Cookie httpOnly émis par `/api/auth/unlock`.",
        },
        "csrfToken": {
            "type": "apiKey",
            "in": "header",
            "name": "X-CSRF-Token",
            "description": "Obligatoire avec le cookie pour les mutations, avec Origin valide.",
        },
        "mobileBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JARVIS mobile token",
        },
        "visualReadBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "scoped visual:read token",
            "description": "Jeton de service local limité au relais visuel en lecture seule.",
        },
        "deviceToken": {"type": "apiKey", "in": "header", "name": "X-Device-Token"},
        "locationToken": {"type": "apiKey", "in": "header", "name": "X-Location-Token"},
    })
    common_responses = components.setdefault("responses", {})
    common_responses.update({
        "Unauthorized": _error_response("Authentification absente ou invalide."),
        "Forbidden": _error_response("Contrôle CSRF ou autorisation refusé."),
        "SetupRequired": _error_response("Secret JARVIS non configuré."),
        "RateLimited": _error_response("Trop de tentatives ; respecter `Retry-After`."),
    })

    operation_ids: set[str] = set()
    used_tags: set[str] = set()
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            _document_operation(path, method, operation)
            operation_id = str(operation["operationId"])
            if operation_id in operation_ids:
                raise RuntimeError(f"operationId OpenAPI dupliqué : {operation_id}")
            operation_ids.add(operation_id)
            used_tags.update(str(tag) for tag in operation.get("tags", []))

    schema["tags"] = [
        {
            "name": tag,
            "description": _TAG_DESCRIPTIONS.get(
                tag,
                f"Opérations du domaine JARVIS « {tag} ».",
            ),
        }
        for tag in sorted(used_tags)
    ]
    return schema


def install_openapi(app: FastAPI) -> None:
    """Installe une génération cachée et réinitialisable par les tests/outils."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi_schema(app)
        return app.openapi_schema

    app.openapi_schema = None
    app.openapi = custom_openapi  # type: ignore[method-assign]


def render_openapi_docs(schema: dict[str, Any]) -> str:
    """Produit une documentation HTML autonome, sans JavaScript ni CDN."""
    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            tag = str((operation.get("tags") or ["autres"])[0])
            grouped[tag].append((path, method.upper(), operation))

    sections: list[str] = []
    for tag in sorted(grouped):
        rows = []
        for path, method, operation in sorted(grouped[tag], key=lambda item: (item[0], item[1])):
            rows.append(
                "<tr>"
                f"<td><code>{escape(method)}</code></td>"
                f"<td><code>{escape(path)}</code></td>"
                f"<td>{escape(str(operation.get('summary') or ''))}</td>"
                f"<td><code>{escape(str(operation.get('x-jarvis-authentication') or ''))}</code></td>"
                f"<td><code>{escape(str(operation.get('operationId') or ''))}</code></td>"
                "</tr>"
            )
        sections.append(
            f"<section><h2>{escape(tag)}</h2><table><thead><tr>"
            "<th>Méthode</th><th>Chemin</th><th>Résumé</th><th>Authentification</th>"
            f"<th>operationId</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
        )

    version = escape(str(schema.get("info", {}).get("version") or ""))
    return (
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>JARVIS Developer API</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;"
        "color:#172033;background:#f6f8fb}h1,h2{color:#101828}section{margin:2rem 0}"
        "table{width:100%;border-collapse:collapse;background:white}th,td{padding:.65rem;"
        "border:1px solid #d8dee9;text-align:left;vertical-align:top}code{overflow-wrap:anywhere}"
        "a{color:#175cd3}</style></head><body>"
        f"<h1>JARVIS Developer API <small>v{version}</small></h1>"
        "<p>Contrat OpenAPI déterministe de l’instance. "
        "<a href=\"./openapi.json\">Télécharger le JSON OpenAPI</a>.</p>"
        "<h2>Authentification</h2><p>Les lectures privées utilisent le cookie de session "
        "ou, sur les routes compatibles, un jeton mobile Bearer. Les mutations par cookie "
        "exigent aussi <code>X-CSRF-Token</code> et une origine autorisée. Chaque opération "
        "indique sa frontière exacte.</p>"
        f"{''.join(sections)}</body></html>"
    )
