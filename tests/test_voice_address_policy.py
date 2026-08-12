"""Politique d'adresse vocale — l'honorifique est rationné, pas supprimé.

Ces tests portent sur la règle elle-même : quels énoncés ont droit à
« Monsieur », combien de fois, et surtout **ce que le filtre ne doit jamais
toucher**. Une réécriture globale du mot casserait une citation, un titre
d'œuvre ou la civilité d'un tiers ; c'est la seule façon dont ce lot pouvait
dégrader une réponse, donc la partie la plus couverte.
"""

from __future__ import annotations

import pytest

from jarvis.voice.address import (
    MODE_FREE,
    MODE_NEVER,
    MODE_RARE,
    VoiceAddressPolicy,
    VoiceSession,
    VoiceUtterance,
    VoiceUtteranceKind,
    apply_address_policy,
    close_voice_session,
    get_voice_session,
    reset_voice_sessions,
    strip_honorific,
)


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_voice_sessions()
    yield
    reset_voice_sessions()


# ── Filtre déterministe ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Bien, Monsieur.", "Bien."),
        ("Très bien, Monsieur. Je lance l'analyse.", "Je lance l'analyse."),
        ("Il fait 18 degrés à Lille, Monsieur.", "Il fait 18 degrés à Lille."),
        ("Il fait 18 degrés Monsieur.", "Il fait 18 degrés."),
        ("Je vous écoute, Monsieur.", "Je vous écoute."),
        ("Je regarde, Monsieur.", "Je regarde."),
        ("Je n'ai pas obtenu de réponse, Monsieur.", "Je n'ai pas obtenu de réponse."),
        ("Bien Monsieur, je reste en veille.", "Je reste en veille."),
        ("Monsieur, votre agenda est vide.", "Votre agenda est vide."),
        ("Bonjour Monsieur. Que puis-je faire pour vous ?",
         "Bonjour. Que puis-je faire pour vous ?"),
        ("Oui Monsieur ?", "Oui ?"),
        ("Désolé Monsieur, l'action a échoué.", "Désolé, l'action a échoué."),
    ],
)
def test_strip_honorific_removes_the_vocative(source: str, expected: str) -> None:
    assert strip_honorific(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # Civilité d'un tiers : ce n'est pas une adresse à l'utilisateur.
        "Monsieur Dupont a rappelé.",
        "Bien, Monsieur Dupont vous attend.",
        # Nom commun.
        "Ce monsieur attend depuis dix minutes.",
        "Un monsieur a laissé un message.",
        # Sujet grammatical en tête de phrase.
        "Monsieur a couru toute la journée.",
        # Citation, titre d'œuvre, contenu lu depuis un document.
        "Le tableau est « Bonjour Monsieur » de Courbet.",
        'Tu as dit "merci Monsieur" hier.',
        "Le fichier `bonjour-monsieur.txt` est introuvable.",
        # Rien à filtrer.
        "Il fait 18 degrés à Lille.",
        "",
    ],
)
def test_strip_honorific_leaves_legitimate_uses_intact(source: str) -> None:
    assert strip_honorific(source) == source


def test_strip_honorific_is_idempotent() -> None:
    once = strip_honorific("Bien, Monsieur. Il fait 18 degrés, Monsieur.")
    assert strip_honorific(once) == once


def test_strip_honorific_never_empties_an_utterance() -> None:
    """Une chaîne vide serait lue par l'appelant comme « ne rien dire »."""
    assert strip_honorific("Monsieur.") != ""
    assert strip_honorific("Monsieur") != ""


def test_strip_honorific_preserves_french_spacing() -> None:
    """L'espace avant « ? ! ; : » est une règle typographique, et le TTS s'en sert."""
    assert strip_honorific("Que puis-je pour vous, Monsieur ?").endswith("vous ?")


# ── Politique par type d'énoncé ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind",
    [
        VoiceUtteranceKind.ANSWER,
        VoiceUtteranceKind.ACTION_CONFIRMATION,
        VoiceUtteranceKind.PROGRESS,
        VoiceUtteranceKind.ERROR,
        VoiceUtteranceKind.SYSTEM_SIGNAL,
    ],
)
def test_ordinary_kinds_never_carry_the_honorific(kind: VoiceUtteranceKind) -> None:
    assert "Monsieur" not in apply_address_policy(
        "C'est fait, Monsieur.", kind=kind, allow_honorific=True, session_boundary=True,
    )


@pytest.mark.parametrize(
    "kind",
    [VoiceUtteranceKind.GREETING, VoiceUtteranceKind.FAREWELL, VoiceUtteranceKind.RITUAL],
)
def test_session_boundaries_may_carry_the_honorific(kind: VoiceUtteranceKind) -> None:
    assert apply_address_policy(
        "Bonjour Monsieur.", kind=kind, allow_honorific=True,
    ) == "Bonjour Monsieur."


def test_boundary_kind_without_permission_is_still_filtered() -> None:
    """Le type ne suffit pas : l'appelant doit affirmer la frontière de session."""
    assert apply_address_policy(
        "Bonjour Monsieur.", kind=VoiceUtteranceKind.GREETING,
    ) == "Bonjour."


def test_honorific_is_granted_once_per_session() -> None:
    session = VoiceSession(session_id=42)
    first = apply_address_policy(
        "Bonjour Monsieur.", kind=VoiceUtteranceKind.GREETING,
        session=session, allow_honorific=True,
    )
    second = apply_address_policy(
        "Me revoici, Monsieur.", kind=VoiceUtteranceKind.GREETING,
        session=session, allow_honorific=True,
    )
    assert first == "Bonjour Monsieur."
    assert "Monsieur" not in second


def test_closing_a_session_restores_the_budget() -> None:
    session = get_voice_session("conv-1")
    assert session is not None
    session.spend_honorific()
    close_voice_session("conv-1")
    reopened = get_voice_session("conv-1")
    assert reopened is not None
    assert reopened.honorific_spent is False


def test_unknown_session_is_not_invented() -> None:
    assert get_voice_session(None) is None


def test_session_registry_is_bounded() -> None:
    """Un processus qui tourne des semaines ne doit pas accumuler les sessions."""
    for index in range(200):
        get_voice_session(f"conv-{index}")
    from jarvis.voice import address

    assert len(address._sessions) <= address._MAX_TRACKED_SESSIONS


# ── Modes ───────────────────────────────────────────────────────────────────


def test_mode_never_filters_even_a_session_opening() -> None:
    policy = VoiceAddressPolicy(mode=MODE_NEVER)
    utterance = VoiceUtterance(
        text="Bonjour Monsieur.",
        kind=VoiceUtteranceKind.GREETING,
        allow_honorific=True,
    )
    assert policy.apply(utterance).text == "Bonjour."


def test_mode_free_disables_the_filter_entirely() -> None:
    policy = VoiceAddressPolicy(mode=MODE_FREE)
    utterance = VoiceUtterance(text="C'est fait, Monsieur.")
    assert policy.apply(utterance).text == "C'est fait, Monsieur."


def test_mode_comes_from_config_and_falls_back_to_rare(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "VOICE_ADDRESS_POLICY", "n'importe quoi", raising=False)
    assert VoiceAddressPolicy().mode == MODE_RARE
    monkeypatch.setattr(config, "VOICE_ADDRESS_POLICY", "never", raising=False)
    assert VoiceAddressPolicy().mode == MODE_NEVER


def test_apply_returns_the_same_object_when_nothing_changes() -> None:
    """Pas de copie inutile sur le chemin chaud d'un tour de parole."""
    utterance = VoiceUtterance(text="Il fait 18 degrés.")
    assert VoiceAddressPolicy(mode=MODE_RARE).apply(utterance) is utterance


# ── Cartographie des producteurs ────────────────────────────────────────────

_VOICE_PRODUCERS = (
    "api/voice_fastpath.py",
    "api/voice_processing.py",
    "api/voice_support.py",
    "api/voice_cognitive.py",
    "api/voice_prompts.py",
    "api/mobile_voice_service.py",
    "api/chat_cognitive.py",
    "api/chat_processing.py",
    "api/action_confirmations.py",
    "audio/tts_cache.py",
    "scripts/audio_daemon.py",
)

# Le seul énoncé de la pile vocale qui porte encore l'honorifique en dur : une
# sortie de veille, c'est-à-dire une véritable réouverture de session. Il est
# joué par ``_play_tts(kind=GREETING)``, donc soumis au budget de session.
_ALLOWED_SPOKEN_HONORIFICS = {"Me revoici, Monsieur."}

# Constantes qui contiennent le mot sans jamais le prononcer : les prompts, qui
# énoncent la règle au modèle, et les motifs de détection anti-boucle, qui
# reconnaissent une réponse de JARVIS renvoyée en entrée.
_NON_SPOKEN_CONSTANTS = {
    "VOICE_ADDRESS_OVERLAY",
    "VOICE_PERSONA_TEMPLATE",
    "jarvis_patterns",
}


def _spoken_string_literals(source: str) -> list[tuple[int, str]]:
    """Chaînes littérales d'un module, hors docstrings et hors prompts.

    Une analyse syntaxique plutôt qu'une recherche ligne à ligne : les
    docstrings de ce lot décrivent précisément le défaut corrigé, donc citent
    « Bien, Monsieur. ». Les confondre avec des phrases prononcées rendrait le
    garde-fou inutilisable — ou, pire, pousserait à ne plus documenter la cause.
    """
    import ast

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    skipped: set[int] = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if any(name in _NON_SPOKEN_CONSTANTS for name in targets):
            for child in ast.walk(node):
                skipped.add(id(child))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or id(node) in skipped:
            continue
        found.append((node.lineno, node.value))
    return found


def test_no_voice_producer_hardcodes_an_ordinary_honorific() -> None:
    """Le filtre est une garantie, pas une excuse pour laisser des chaînes fausses.

    Une phrase en dur qui porte l'honorifique serait réécrite à la lecture : le
    texte prononcé ne correspondrait plus au texte persisté, affiché et mis en
    cache. On corrige la source, et ce test empêche la dérive.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for relative in _VOICE_PRODUCERS:
        source = (root / relative).read_text(encoding="utf-8")
        for lineno, literal in _spoken_string_literals(source):
            if "onsieur" not in literal:
                continue
            if literal.strip() in _ALLOWED_SPOKEN_HONORIFICS:
                continue
            offenders.append(f"{relative}:{lineno}: {literal.strip()[:80]}")

    assert offenders == [], (
        "honorifique en dur dans un producteur vocal :\n" + "\n".join(offenders)
    )
