"""Reçus d'approbation effectful conservés exclusivement côté parent JARVIS."""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from .capabilities import CapabilityEnvelope, CapabilityError
from .idempotency import canonical_digest

MAX_APPROVAL_TTL_SECONDS = 600
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}$")


def _clean_identifier(value: str, *, label: str) -> str:
    clean = str(value).strip()
    if not _IDENTIFIER.fullmatch(clean):
        raise CapabilityError(f"approval_{label}_invalid")
    return clean


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CapabilityError("approval_arguments_invalid")
            result[key] = _plain_json(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CapabilityError("approval_arguments_invalid")


def _arguments_digest(arguments: Mapping[str, Any]) -> str:
    if "_jarvis" in arguments:
        raise CapabilityError("approval_arguments_reserved")
    return canonical_digest(_plain_json(arguments))


def arguments_digest(arguments: Mapping[str, Any]) -> str:
    """Digest canonique des arguments métier, hors métadonnées `_jarvis`."""

    return _arguments_digest(arguments)


def _expiry_timestamp(value: datetime | float) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CapabilityError("approval_expiration_invalid")
        timestamp = value.timestamp()
    elif isinstance(value, bool):
        raise CapabilityError("approval_expiration_invalid")
    else:
        timestamp = float(value)
    if not math.isfinite(timestamp):
        raise CapabilityError("approval_expiration_invalid")
    return timestamp


@dataclass(slots=True)
class _ApprovalGrant:
    approval_id: str
    run_id: str
    tool_name: str
    arguments_digest: str
    expires_at: float
    state: str = "issued"


class ApprovalLedger:
    """Autorité one-shot non sérialisée, liée au run, outil et arguments exacts."""

    def __init__(self, capability: CapabilityEnvelope) -> None:
        capability.validate()
        self._capability = capability
        self._grants: dict[str, _ApprovalGrant] = {}
        self._guard = threading.RLock()

    def grant(
        self,
        *,
        approval_id: str,
        run_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        expires_at: datetime | float,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            raise CapabilityError("approval_time_invalid")
        clean_id = _clean_identifier(approval_id, label="id")
        clean_run = _clean_identifier(run_id, label="run")
        clean_tool = _clean_identifier(tool_name, label="tool")
        if clean_run != self._capability.run_id:
            raise CapabilityError("approval_run_mismatch")
        self._capability.validate(expected_run_id=clean_run, now=current)
        expiration = _expiry_timestamp(expires_at)
        if (
            expiration <= current
            or expiration > current + MAX_APPROVAL_TTL_SECONDS
            or expiration > self._capability.expires_at
        ):
            raise CapabilityError("approval_expiration_invalid")
        digest = _arguments_digest(arguments)
        grant = _ApprovalGrant(
            approval_id=clean_id,
            run_id=clean_run,
            tool_name=clean_tool,
            arguments_digest=digest,
            expires_at=expiration,
        )
        with self._guard:
            existing = self._grants.get(clean_id)
            if existing is not None:
                if existing == grant:
                    return
                raise CapabilityError("approval_id_conflict")
            self._grants[clean_id] = grant

    def revoke(self, *, approval_id: str, run_id: str) -> bool:
        clean_id = _clean_identifier(approval_id, label="id")
        clean_run = _clean_identifier(run_id, label="run")
        if clean_run != self._capability.run_id:
            raise CapabilityError("approval_run_mismatch")
        with self._guard:
            grant = self._grants.get(clean_id)
            if grant is None:
                return False
            if grant.run_id != clean_run:
                raise CapabilityError("approval_run_mismatch")
            if grant.state == "reserved":
                raise CapabilityError("approval_effect_in_progress_or_ambiguous")
            grant.state = "revoked"
            return True

    def revoke_all(self) -> int:
        """Retire atomiquement toute autorité, notamment aux frontières terminales."""
        with self._guard:
            count = len(self._grants)
            for grant in self._grants.values():
                grant.state = "revoked"
            self._grants.clear()
            return count

    def is_visible(self, tool_name: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        with self._guard:
            return any(
                grant.tool_name == tool_name
                and grant.state == "issued"
                and current < grant.expires_at
                for grant in self._grants.values()
            )

    def execute(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        operation: Callable[[], dict[str, Any]],
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        clean_tool = _clean_identifier(tool_name, label="tool")
        digest = _arguments_digest(arguments)
        with self._guard:
            tool_grants = [
                grant
                for grant in self._grants.values()
                if grant.tool_name == clean_tool and grant.state != "revoked"
            ]
            valid = [grant for grant in tool_grants if current < grant.expires_at]
            matching = [grant for grant in valid if grant.arguments_digest == digest]
            if not matching:
                if tool_grants and not valid:
                    raise CapabilityError("tool_approval_expired")
                if valid:
                    raise CapabilityError("tool_approval_arguments_mismatch")
                raise CapabilityError("tool_approval_required")
            grant = matching[0]
            if grant.state == "reserved":
                raise CapabilityError("approval_effect_in_progress_or_ambiguous")
            if grant.state == "completed":
                raise CapabilityError("tool_approval_consumed")
            if grant.state == "issued":
                grant.state = "reserved"
            else:
                raise CapabilityError("tool_approval_required")

        # L'opération effectful réserve son journal durable avant l'effet. Toute
        # exception laisse le reçu réservé : un crash ambigu ne peut être rejoué.
        result = operation()
        if not isinstance(result, dict):
            raise CapabilityError("tool_result_invalid")
        with self._guard:
            if grant.state == "reserved":
                grant.state = "completed"
        return result
