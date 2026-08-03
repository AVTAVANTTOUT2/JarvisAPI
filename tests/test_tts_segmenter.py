"""Découpage du flux LLM en segments prononçables.

Ces cas ne sont pas décoratifs : chacun correspond à une faute qui s'entend.
Couper « 18.5 » en deux fait dire « dix-huit. Cinq », couper « M. Dupont » fait
marquer une fin de phrase au milieu d'un nom, et attendre une ponctuation qui
ne vient jamais fait taire JARVIS.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from jarvis.audio.tts import TextStreamSegmenter, load_tts_settings, segment_stream


@pytest.fixture()
def settings():
    return replace(
        load_tts_settings(),
        min_chunk_chars=30,
        target_chunk_chars=80,
        max_chunk_chars=180,
        flush_timeout_ms=40,
    )


def _rejoin(parts: list[str]) -> str:
    """Contenu prononcé, indépendamment du découpage."""
    return " ".join(parts).replace("  ", " ").strip()


def _segments(text: str, settings, *, per_char: bool = True) -> list[str]:
    segmenter = TextStreamSegmenter(settings)
    out: list[str] = []
    if per_char:
        for char in text:
            out += segmenter.feed(char)
    else:
        out += segmenter.feed(text)
    return out + segmenter.flush()


# ── Coupures nominales ───────────────────────────────────────────────────────


def test_splits_on_sentence_end(settings):
    """Après le premier segment, la phrase entière est l'unité naturelle."""
    text = (
        "Bonjour Monsieur. Il fait dix-huit degrés à Lille, couvert. "
        "Parapluie cet après-midi."
    )
    parts = _segments(text, settings)
    assert parts[0] == "Bonjour Monsieur."
    assert parts[1:] == [
        "Il fait dix-huit degrés à Lille, couvert.",
        "Parapluie cet après-midi.",
    ]


def test_short_sentence_waits_for_more_text(settings):
    """Un fragment sous le minimum coûte une génération pour un souffle."""
    segmenter = TextStreamSegmenter(settings)
    assert segmenter.feed("Bien.") == []
    assert segmenter.pending == "Bien."


def test_flush_emits_the_remainder_without_punctuation(settings):
    segmenter = TextStreamSegmenter(settings)
    segmenter.feed("Une phrase sans ponctuation finale")
    assert segmenter.flush() == ["Une phrase sans ponctuation finale"]


def test_question_and_exclamation_are_boundaries(settings):
    text = "Souhaitez-vous que je vous rappelle demain matin ? Bien, c'est noté."
    assert len(_segments(text, settings)) == 2


def test_comma_only_splits_past_the_target_size(settings):
    """Hors premier segment, une virgule est une respiration, pas une fin."""
    text = (
        "Bonjour Monsieur. Voici enfin la liste des éléments, courte, "
        "que vous m'avez demandée."
    )
    parts = _segments(text, settings)
    assert parts[0] == "Bonjour Monsieur."
    # Le reste tient sous la taille cible : aucune coupure sur les virgules.
    assert parts[1] == "Voici enfin la liste des éléments, courte, que vous m'avez demandée."


# ── Régions insécables ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "unbreakable"),
    [
        ("Le total de la commande atteint 18.5 euros ce mois-ci exactement.", "18.5"),
        ("Écrivez à support.technique@exemple.fr dès que possible.", "support.technique@exemple.fr"),
        (
            "Le détail se trouve sur https://exemple.fr/page.html?a=1 aussi.",
            "https://exemple.fr/page.html?a=1",
        ),
        ("M. Dupont attend une réponse avant la fin de la journée.", "M. Dupont"),
        ("La distance restante est de 12 km avant le prochain arrêt.", "12 km"),
        ("Le fichier fait 4.2 Go et doit être transféré ce soir.", "4.2 Go"),
        ("Le rapport (voir p. 12) doit être relu avant la réunion.", "(voir p. 12)"),
    ],
    ids=[
        "decimal",
        "email",
        "url",
        "abreviation",
        "nombre_unite",
        "taille_fichier",
        "parenthese_courte",
    ],
)
def test_never_splits_inside_an_unbreakable_region(text: str, unbreakable: str, settings):
    """La coupure peut tomber ailleurs — jamais au milieu de ce groupe."""
    parts = _segments(text, settings)
    assert any(unbreakable in part for part in parts), parts
    assert _rejoin(parts) == text


def test_ellipsis_is_not_three_boundaries(settings):
    """Couper entre deux points produirait deux segments vides à prononcer."""
    text = "Je vérifie votre agenda... puis je vous réponds dans un instant."
    parts = _segments(text, settings)
    assert any("agenda..." in part for part in parts), parts
    assert all(part.strip() for part in parts)


# ── Premier segment : le seul dont la longueur se paie en silence ───────────


def test_first_segment_is_shorter_than_the_others(settings):
    """Mesuré : sans cette règle, une phrase de 94 caractères sans point
    interne fait attendre 564 ms au lieu de ~240 ms."""
    text = (
        "Il fait dix-huit degrés à Lille, ciel couvert, et une averse est "
        "attendue en fin d'après-midi."
    )
    parts = _segments(text, settings)
    assert len(parts) > 1
    assert len(parts[0]) <= settings.first_chunk_max_chars
    assert parts[0].endswith(",")
    assert _rejoin(parts) == text


def test_first_segment_still_refuses_a_syllable(settings):
    """Un fragment d'un mot coûterait une génération complète pour un souffle."""
    segmenter = TextStreamSegmenter(settings)
    assert segmenter.feed("Bien.") == []
    # Le point final n'est pas encore suivi d'un caractère : il peut être le
    # premier d'une suite de points. On attend la preuve que la phrase est finie.
    assert segmenter.feed(" Voici la suite de la réponse.") == []
    assert segmenter.feed(" ") == ["Bien. Voici la suite de la réponse."]


def test_only_the_first_segment_uses_the_short_thresholds(settings):
    text = (
        "Bonjour Monsieur. Voici un paragraphe long, avec des virgules, "
        "qui ne doit pas être haché en morceaux minuscules après le début."
    )
    parts = _segments(text, settings)
    assert parts[0] == "Bonjour Monsieur."
    assert all(len(part) >= settings.min_chunk_chars for part in parts[1:])


def test_code_block_stays_whole(settings):
    text = "Voici la commande à lancer : ```git status. git commit -m 'x'``` voilà."
    parts = _segments(text, settings)
    assert any("git status. git commit" in part for part in parts)


# ── Plafond et flux ──────────────────────────────────────────────────────────


def test_long_text_without_punctuation_is_cut_on_a_space(settings):
    text = "mot " * 80
    parts = _segments(text, settings)
    assert len(parts) > 1
    assert all(len(part) <= settings.max_chunk_chars for part in parts[:-1])
    assert "".join(part.replace(" ", "") for part in parts) == text.replace(" ", "")


def test_a_single_giant_token_is_never_mutilated(settings):
    """Couper une URL géante produirait une prononciation absurde."""
    token = "https://exemple.fr/" + "a" * 400
    segmenter = TextStreamSegmenter(settings)
    assert segmenter.feed(token) == []
    assert segmenter.flush() == [token]


def test_segments_arrive_before_the_stream_ends(settings):
    """La première phrase doit partir sans attendre la fin de la réponse."""
    segmenter = TextStreamSegmenter(settings)
    emitted = segmenter.feed(
        "Il fait dix-huit degrés à Lille, couvert. Et ensuite, je poursuis"
    )
    assert emitted == ["Il fait dix-huit degrés à Lille, couvert."]
    assert segmenter.pending.strip() == "Et ensuite, je poursuis"


@pytest.mark.asyncio
async def test_flush_timeout_speaks_what_is_already_written(settings):
    """Une pause du modèle ne doit pas retarder ce qui est déjà écrit."""

    async def _deltas():
        yield "Voici une phrase déjà complète et suffisamment longue"
        await asyncio.sleep(0.25)
        yield " et la suite arrive enfin ici."

    out: list[str] = []
    async for segment in segment_stream(_deltas(), settings=settings):
        out.append(segment)

    assert len(out) == 2
    assert out[0] == "Voici une phrase déjà complète et suffisamment longue"


@pytest.mark.asyncio
async def test_stream_preserves_order_and_content(settings):
    parts = [
        "Bonjour Monsieur. ",
        "Trois messages vous attendent ce matin. ",
        "Le premier vient de votre école.",
    ]

    async def _deltas():
        for part in parts:
            yield part

    out = [segment async for segment in segment_stream(_deltas(), settings=settings)]
    assert " ".join(out) == "".join(parts).strip()


def test_thresholds_are_reordered_when_misconfigured(monkeypatch):
    """Une valeur saisie à l'envers ne doit pas rendre JARVIS muet."""
    monkeypatch.setattr("config.TTS_MIN_CHUNK_CHARS", 200, raising=False)
    monkeypatch.setattr("config.TTS_MAX_CHUNK_CHARS", 20, raising=False)
    resolved = load_tts_settings()
    assert resolved.min_chunk_chars <= resolved.target_chunk_chars
    assert resolved.target_chunk_chars <= resolved.max_chunk_chars
