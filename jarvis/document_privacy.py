"""Frontière de confidentialité des traitements documentaires locaux/cloud."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

import config
from database import get_setting, set_setting
from jarvis.models import DataSource
from jarvis.router import JARVISRouter

logger = logging.getLogger(__name__)

STRICT_LOCAL_SETTING = "document_strict_local"
SUMMARY_MIN_CHARS = 500


class DocumentCloudBlocked(ValueError):
    """Le document ne peut pas quitter la machine avec la politique active."""


@dataclass(frozen=True)
class DocumentSummaryResult:
    summary: str | None
    processing_mode: str
    cloud_consent: bool
    cloud_request_attempted: bool
    data_left_device: bool
    pii_entities_masked: int
    cloud_payload_chars: int

    def as_dict(self) -> dict:
        return asdict(self)


def document_strict_local_enabled() -> bool:
    """Retourne le réglage dynamique, avec la config comme défaut sûr."""
    default = "true" if config.DOCUMENT_STRICT_LOCAL else "false"
    value = get_setting(STRICT_LOCAL_SETTING, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def set_document_strict_local(enabled: bool) -> None:
    set_setting(STRICT_LOCAL_SETTING, "true" if enabled else "false")


def get_document_privacy_policy() -> dict:
    """Décrit explicitement les flux de données documentaires de JARVIS."""
    strict_local = document_strict_local_enabled()
    cloud_max_chars = max(1, int(config.DOCUMENT_CLOUD_MAX_CHARS))
    return {
        "mode": "strict_local" if strict_local else "hybrid",
        "strict_local": strict_local,
        "cloud_provider": "DeepSeek",
        "cloud_summary_available": not strict_local,
        "explicit_consent_required": True,
        "cloud_max_chars": cloud_max_chars,
        "pii_protection": "pseudonymisation réversible locale avant envoi",
        "features": {
            "school_upload": {
                "storage": "local",
                "extraction": "local",
                "summary": "none",
                "data_leaving_device": "none",
            },
            "conversation_document": {
                "storage": "local",
                "extraction": "local",
                "default_summary": "local_extractive",
                "cloud_summary": (
                    "blocked"
                    if strict_local
                    else "optional_with_per_upload_consent_and_pii_masking"
                ),
                "cloud_chat_context": (
                    "blocked"
                    if strict_local
                    else "only_consented_documents_with_pii_masking"
                ),
                "data_leaving_device": (
                    "none"
                    if strict_local
                    else "up_to_cloud_max_chars_for_summary_and_chat_only_after_explicit_consent"
                ),
            },
        },
    }


def local_document_summary(text: str, *, max_chars: int = 600) -> str | None:
    """Produit un bref résumé extractif sans modèle ni appel réseau."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if len(normalized) <= SUMMARY_MIN_CHARS:
        return None
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if sentence.strip()
    ]
    selected = " ".join(sentences[:3]) if sentences else normalized
    if len(selected) <= max_chars:
        return selected
    return selected[: max_chars - 1].rstrip() + "…"


def ensure_cloud_summary_allowed(cloud_consent: bool) -> None:
    """Refuse un consentement cloud incompatible avec le mode strict local."""
    if cloud_consent and document_strict_local_enabled():
        raise DocumentCloudBlocked(
            "Le mode strictement local interdit l'envoi cloud des documents"
        )


async def summarize_document(
    text: str,
    *,
    cloud_consent: bool,
    router: JARVISRouter | None = None,
) -> DocumentSummaryResult:
    """Résume localement par défaut, ou via DeepSeek anonymisé avec consentement."""
    ensure_cloud_summary_allowed(cloud_consent)
    local_summary = local_document_summary(text)
    if not local_summary:
        return DocumentSummaryResult(
            summary=None,
            processing_mode="local",
            cloud_consent=cloud_consent,
            cloud_request_attempted=False,
            data_left_device=False,
            pii_entities_masked=0,
            cloud_payload_chars=0,
        )

    if not cloud_consent:
        return DocumentSummaryResult(
            summary=local_summary,
            processing_mode="local",
            cloud_consent=False,
            cloud_request_attempted=False,
            data_left_device=False,
            pii_entities_masked=0,
            cloud_payload_chars=0,
        )

    payload = text[: max(1, int(config.DOCUMENT_CLOUD_MAX_CHARS))]
    active_router = router
    request_attempted = False
    try:
        active_router = active_router or JARVISRouter()
        request_attempted = True
        summary = await active_router.summarize(payload, DataSource.DOCUMENT)
        return DocumentSummaryResult(
            summary=summary.strip() or local_summary,
            processing_mode="cloud_anonymized",
            cloud_consent=True,
            cloud_request_attempted=True,
            data_left_device=True,
            pii_entities_masked=active_router.stats.pii_entities_masked,
            cloud_payload_chars=len(payload),
        )
    except Exception as exc:
        # L'upload reste utilisable. On déclare conservativement qu'une requête
        # a été tentée : une erreur réseau peut survenir après émission du corps.
        logger.warning("[document privacy] résumé cloud impossible : %s", exc)
        pii_entities_masked = (
            active_router.stats.pii_entities_masked if active_router is not None else 0
        )
        return DocumentSummaryResult(
            summary=local_summary,
            processing_mode="local_fallback",
            cloud_consent=True,
            cloud_request_attempted=request_attempted,
            data_left_device=request_attempted,
            pii_entities_masked=pii_entities_masked,
            cloud_payload_chars=len(payload) if request_attempted else 0,
        )
