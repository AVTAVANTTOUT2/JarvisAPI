"""Navigateur agentique de recherche, sans mutation externe ni authentification."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from core.outbound_security import OutboundURLRejected
from integrations.browser_driver import BrowserElement
from integrations.browser_runtime import (
    BrowserError,
    BrowserSession,
    browser_now,
    clear_browser_receipt,
    close_session,
    discard_session,
    get_browser_snapshot_artifact,
    get_session,
    record_browser_receipt,
    release_session,
    run_browser_coroutine,
    set_driver_factory,
    shutdown,
    validate_browser_target,
    validate_target,
)
from integrations.browser_security import (
    BrowserSecurityError,
    sanitized_browser_path,
    sanitized_browser_url,
)

logger = logging.getLogger("jarvis.browser")

BROWSER_TOOL_NAME = "jarvis_browser"
MAX_TYPE_CHARS = 500
_SESSION_REF_RE = re.compile(r"^s[0-9]+e[0-9]+$")
_COMMIT_RE = re.compile(
    r"(?i)\b(pay|payment|payer|acheter|checkout|billing|order|commande|"
    r"book|booking|r[ée]server|reserva|reservar|pagar|kaufen|bestellen|"
    r"confirm|confirmer|delete|supprimer|remove|publish|publier|send|envoyer)\b"
)
_SENSITIVE_AUTOCOMPLETE = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "username",
        "webauthn",
        "cc-name",
        "cc-number",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
        "cc-csc",
        "transaction-amount",
        "transaction-currency",
    }
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)\b(password|passcode|mot de passe|otp|2fa|verification code|"
    r"one.?time|credit card|card number|cvv|cvc|iban|bank account|routing|"
    r"social security|national id|passport|login|sign.?in|connexion)\b"
)

BROWSER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": ["open", "see", "search", "close"],
        },
        "url": {"type": ["string", "null"], "maxLength": 2000},
        "ref": {"type": ["string", "null"], "maxLength": 32},
        "snapshot_id": {"type": ["string", "null"], "maxLength": 64},
        "element_name": {"type": ["string", "null"], "maxLength": 80},
        "page_origin": {"type": ["string", "null"], "maxLength": 512},
        "target_origin": {"type": ["string", "null"], "maxLength": 512},
        "target_path": {"type": ["string", "null"], "maxLength": 512},
        "target_sha256": {"type": ["string", "null"], "maxLength": 64},
        "text": {"type": ["string", "null"], "maxLength": MAX_TYPE_CHARS},
    },
    "required": ["op"],
    "additionalProperties": False,
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _element_summary(item: BrowserElement) -> dict[str, str]:
    result = {"ref": item.ref, "role": item.role, "name": item.name}
    target = item.href
    if not target and (
        item.tag == "input"
        and (item.input_type == "search" or item.role == "searchbox")
    ):
        target = item.form_action
        if target:
            result["action"] = "search_get"
    if target:
        result["target_origin"] = sanitized_browser_url(target)
        result["target_path"] = sanitized_browser_path(target)
        result["target_sha256"] = _sha256(target)
    return result


def is_final_commit(name: str, url: str) -> bool:
    """Filtre sémantique supplémentaire ; la structure DOM reste l'autorité."""

    try:
        path = urlsplit(str(url or "")).path
    except ValueError:
        path = ""
    return bool(_COMMIT_RE.search(f"{name} {path}"))


def _snapshot(
    session: BrowserSession, url: str, title: str, text: str, snapshot_id: str
) -> dict[str, Any]:
    return {
        "url": sanitized_browser_url(url),
        "title": title,
        "text": text,
        "snapshot_id": snapshot_id,
        "elements": [_element_summary(item) for item in session.elements.values()],
    }


async def _observe(session: BrowserSession, *, operation: str) -> dict[str, Any]:
    url, title, text, elements = await session.driver.observe()
    session.generation += 1
    snapshot_id = uuid4().hex
    bound: dict[str, BrowserElement] = {}
    for ordinal, item in enumerate(elements, start=1):
        ref = f"s{session.generation}e{ordinal}"
        bound[ref] = replace(
            item,
            ref=ref,
            generation=session.generation,
            snapshot_id=snapshot_id,
            page_url=url,
        )
    session.elements = bound
    session.driver.url = url
    session.last_used_at = browser_now()
    record_browser_receipt(
        session.run_id,
        snapshot_id=snapshot_id,
        operation=operation,
        url=url,
        title=title,
        text=text,
        policy_result="allowed",
    )
    return _snapshot(session, url, title, text, snapshot_id)


def _require_ref(
    session: BrowserSession,
    ref: str | None,
    snapshot_id: str | None,
    element_name: str | None,
    page_origin: str | None,
) -> BrowserElement:
    key = str(ref or "").strip()
    expected_snapshot = str(snapshot_id or "").strip()
    if not expected_snapshot:
        raise BrowserError("snapshot_required", "snapshot_id requis pour agir")
    expected_name = str(element_name or "").strip()
    if not expected_name:
        raise BrowserError("action_binding_required", "nom d'élément requis pour agir")
    expected_origin = str(page_origin or "").strip()
    if not expected_origin:
        raise BrowserError("action_binding_required", "origine de page requise pour agir")
    element = session.elements.get(key)
    if (
        element is not None
        and element.snapshot_id == expected_snapshot
        and element.name == expected_name
        and sanitized_browser_url(element.page_url) == expected_origin
    ):
        return element
    code = "stale_ref" if _SESSION_REF_RE.fullmatch(key) else "unknown_ref"
    raise BrowserError(code, f"référence invalide : {key or '∅'}")


def _sensitive(element: BrowserElement) -> bool:
    autocomplete_tokens = frozenset(element.autocomplete.split())
    description = " ".join(
        (element.name, element.field_name, element.form_action, element.autocomplete)
    )
    return bool(
        element.input_type in {"file", "password"}
        or bool(autocomplete_tokens.intersection(_SENSITIVE_AUTOCOMPLETE))
        or any(token.startswith("cc-") for token in autocomplete_tokens)
        or element.form_has_password
        or _SENSITIVE_FIELD_RE.search(description)
    )


def _allow_fill(element: BrowserElement) -> None:
    if element.disabled or element.readonly:
        raise BrowserError("field_unavailable", "champ indisponible")
    if _sensitive(element):
        raise BrowserError("sensitive_field", "champ sensible interdit")
    if element.contenteditable or element.tag not in {"input", "textarea"}:
        raise BrowserError("field_unclassified", "champ impossible à classifier")
    if element.input_type in {
        "button",
        "checkbox",
        "file",
        "hidden",
        "image",
        "radio",
        "reset",
        "submit",
    }:
        raise BrowserError("field_unclassified", "champ impossible à classifier")


def _validate_open_entrypoint(url: str) -> str:
    """N'autorise qu'une racine d'origine comme point d'entrée explicite.

    Une URL GET opaque, avec chemin ou query, peut représenter une mutation
    serveur mal étiquetée. La recherche est donc le seul mécanisme autorisé
    pour construire une URL avec query, à partir d'un formulaire GET live et
    classifié.
    """

    validated = validate_target(url)
    parsed = urlsplit(validated)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise BrowserError(
            "entrypoint_blocked",
            "open accepte uniquement la racine HTTPS publique d'une origine",
        )
    return validated


def _allow_search_submit(element: BrowserElement) -> None:
    _allow_fill(element)
    if is_final_commit(element.name, element.form_action):
        raise BrowserError("external_effect_blocked", "action finale interdite")
    if (
        element.tag != "input"
        or (element.input_type != "search" and element.role != "searchbox")
        or element.form_method != "get"
        or not element.form_action
        or not element.field_name
        or element.target not in {"", "_self"}
    ):
        raise BrowserError("submit_blocked", "Entrée ne peut pas soumettre ce formulaire")


async def _blocked(
    session: BrowserSession, *, operation: str, error: BrowserError
) -> dict[str, Any]:
    snapshot = await _observe(session, operation=operation)
    record_browser_receipt(
        session.run_id,
        snapshot_id=str(snapshot["snapshot_id"]),
        operation=operation,
        url=session.driver.url,
        title=str(snapshot["title"]),
        text=str(snapshot["text"]),
        policy_result="blocked",
        block_reason=error.code,
    )
    return {
        "ok": False,
        "op": operation,
        "started": True,
        "blocked": error.code,
        "needs_confirmation": False,
        "message": str(error),
        **snapshot,
    }


async def _apply_locked(
    session: BrowserSession, operation: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    if operation == "open":
        await session.driver.open(
            _validate_open_entrypoint(str(arguments.get("url") or ""))
        )
        snapshot = await _observe(session, operation=operation)
        return {"ok": True, "op": operation, "started": True, **snapshot}
    if operation == "see":
        if not session.driver.url:
            raise BrowserError("no_page", "aucune page ouverte")
        snapshot = await _observe(session, operation=operation)
        return {"ok": True, "op": operation, "started": True, **snapshot}
    element = _require_ref(
        session,
        arguments.get("ref") if isinstance(arguments.get("ref"), str) else None,
        (
            arguments.get("snapshot_id")
            if isinstance(arguments.get("snapshot_id"), str)
            else None
        ),
        (
            arguments.get("element_name")
            if isinstance(arguments.get("element_name"), str)
            else None
        ),
        (
            arguments.get("page_origin")
            if isinstance(arguments.get("page_origin"), str)
            else None
        ),
    )
    try:
        live = await session.driver.inspect(element)
    except BrowserSecurityError as exc:
        session.elements.clear()
        raise BrowserError(exc.code, str(exc)) from exc
    try:
        if operation == "search":
            _allow_search_submit(live.element)
            text = str(arguments.get("text") or "")
            expected_origin = str(arguments.get("target_origin") or "").strip()
            expected_path = str(arguments.get("target_path") or "").strip()
            expected_digest = str(arguments.get("target_sha256") or "").strip()
            if not text or len(text) > MAX_TYPE_CHARS:
                raise BrowserError("text_invalid", "recherche invalide")
            if (
                expected_origin != sanitized_browser_url(live.element.form_action)
                or expected_path != sanitized_browser_path(live.element.form_action)
                or expected_path == "[PATH_REDACTED]"
                or expected_digest != _sha256(live.element.form_action)
            ):
                raise BrowserError(
                    "action_binding_required", "destination de recherche requise"
                )
            await session.driver.submit_search(live, text)
        else:
            raise BrowserError("op_invalid", f"opération inconnue : {operation}")
    except BrowserError as exc:
        return await _blocked(session, operation=operation, error=exc)
    snapshot = await _observe(session, operation=operation)
    return {"ok": True, "op": operation, "started": True, **snapshot}


async def _apply(run_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    operation = str(arguments.get("op") or "").strip()
    if operation not in {"open", "see", "search", "close"}:
        raise BrowserError("op_invalid", f"opération inconnue : {operation or '∅'}")
    if operation == "close":
        await discard_session(run_id)
        return {"ok": True, "closed": True, "run_id": run_id}
    if operation == "open":
        _validate_open_entrypoint(str(arguments.get("url") or ""))
    session = await get_session(run_id)
    try:
        async with session.operation_lock:
            if session.closing:
                raise BrowserError("session_closed", "session navigateur fermée")
            return await _apply_locked(session, operation, arguments)
    except asyncio.CancelledError:
        await discard_session(run_id, session)
        clear_browser_receipt(run_id)
        raise
    except (BrowserSecurityError, OutboundURLRejected):
        await discard_session(run_id, session)
        clear_browser_receipt(run_id)
        raise
    except BrowserError:
        raise
    except Exception:
        await discard_session(run_id, session)
        clear_browser_receipt(run_id)
        raise
    finally:
        release_session(session)


def apply(run_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Point d'entrée synchrone pour le pont MCP."""

    clean_run = str(run_id or "").strip()
    if not clean_run or len(clean_run) > 160:
        return {"ok": False, "error": "run_id_invalid", "started": False}
    try:
        return run_browser_coroutine(_apply(clean_run, arguments))
    except (OutboundURLRejected, BrowserSecurityError) as exc:
        close_session(clean_run)
        clear_browser_receipt(clean_run)
        return {
            "ok": False,
            "error": exc.code,
            "message": "destination ou requête refusée par la politique navigateur",
            "started": True,
        }
    except BrowserError as exc:
        if exc.code == "browser_timeout":
            close_session(clean_run)
        return {
            "ok": False,
            "error": exc.code,
            "message": str(exc),
            "started": exc.code != "browser_disabled",
        }
    except Exception as exc:
        close_session(clean_run)
        clear_browser_receipt(clean_run)
        logger.warning("navigateur agentique : %s", type(exc).__name__)
        return {
            "ok": False,
            "error": "browser_failed",
            "message": f"navigateur indisponible ({type(exc).__name__})",
            "started": True,
        }

__all__ = [
    "BROWSER_INPUT_SCHEMA",
    "BROWSER_TOOL_NAME",
    "BrowserElement",
    "BrowserError",
    "apply",
    "clear_browser_receipt",
    "close_session",
    "get_browser_snapshot_artifact",
    "is_final_commit",
    "set_driver_factory",
    "shutdown",
    "validate_browser_target",
]
