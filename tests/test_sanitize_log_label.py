"""Étiquettes de journaux : pas de contenu libre dans les colonnes indexées."""

from __future__ import annotations

from jarvis.log_privacy import sanitize_log_label


def test_sanitize_log_label_accepts_safe_tokens() -> None:
    assert sanitize_log_label("terminal") == "terminal"
    assert sanitize_log_label("mail.send") == "mail.send"
    assert sanitize_log_label("phase_1") == "phase_1"
    assert sanitize_log_label("  agent:voice  ") == "agent:voice"


def test_sanitize_log_label_rejects_content_carriers() -> None:
    assert sanitize_log_label(None) == "unknown"
    assert sanitize_log_label(12) == "unknown"
    assert sanitize_log_label("") == "unknown"
    assert sanitize_log_label("   ") == "unknown"
    assert sanitize_log_label("mail send") == "unknown"
    assert sanitize_log_label("a\nb") == "unknown"
    assert sanitize_log_label("path/to") == "unknown"
    assert sanitize_log_label("Bearer sk-secret") == "unknown"
    assert sanitize_log_label("ok", fallback="other") == "ok"
    assert sanitize_log_label("bad label", fallback="other") == "other"


def test_sanitize_log_label_truncates_before_match() -> None:
    # 65 caractères alphanumériques : tronqué à 64, toujours valide.
    assert sanitize_log_label("a" * 65) == "a" * 64
    # Après troncature, un espace résiduel invalide → fallback.
    assert sanitize_log_label(("b" * 63) + " c") == "unknown"
