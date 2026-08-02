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
    text = "Il fait dix-huit degrés à Lille, couvert. Parapluie cet après-midi."
    assert _segments(text, settings) == [
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
    short = "Bonjour Monsieur, il fait beau."
    assert _segments(short, settings) == [short]

    long_clause = (
        "Voici la liste complète des éléments que vous m'avez demandé de "
        "vérifier ce matin, puis je passerai au reste."
    )
    parts = _segments(long_clause, settings)
    assert len(parts) == 2
    assert parts[0].endswith(",")


# ── Régions insécables ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Le total de la commande atteint 18.5 euros ce mois-ci exactement.",
        "Écrivez à support.technique@exemple.fr dès que possible, merci.",
        "Le détail complet se trouve sur https://exemple.fr/page.html?a=1 aussi.",
        "M. Dupont attend une réponse avant la fin de la journée de travail.",
        "La distance restante est de 12 km avant le prochain arrêt prévu.",
        "Le fichier fait 4.2 Go et doit être transféré avant ce soir absolument.",
        "Le rapport (voir p. 12) doit être relu avant la réunion de demain.",
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
def test_never_splits_inside_an_unbreakable_region(text: str, settings):
    assert _segments(text, settings) == [text]


def test_ellipsis_is_not_three_boundaries(settings):
    text = "Je vérifie votre agenda... puis je vous réponds dans un instant."
    parts = _segments(text, settings)
    assert all(not part.endswith("..") or part == text for part in parts)
    assert "" not in parts


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
