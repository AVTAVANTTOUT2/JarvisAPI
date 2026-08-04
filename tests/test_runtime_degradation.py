"""Trois défauts d'exécution qui se manifestaient par une voix dégradée.

Aucun n'était visible en tant que tel. L'utilisateur entendait une voix qui
coupait et des réponses incohérentes, et soupçonnait le moteur vocal. Les
causes étaient ailleurs :

1. le proxy WebSocket ne relayait pas ``Origin`` : le backend refusait chaque
   connexion en 403, le navigateur reconnectait sans fin, et cette boucle
   serrée disputait le CPU au moteur vocal local ;
2. une réponse vide du modèle était annoncée « Je n'ai pas compris » — ce qui
   accuse la compréhension alors que la transcription était juste ;
3. un ``osascript`` Mail expirant à 30 s était retenté immédiatement, puis
   rappelé toutes les deux minutes, immobilisant un thread la moitié du temps.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── 1. Proxy WebSocket ───────────────────────────────────────────────────────


def test_ws_proxy_forwards_origin_and_host():
    """Sans ``Origin``, le contrôle du backend est inconditionnellement faux.

    ``browser_websocket_origin_allowed()`` refuse toute connexion dont l'Origin
    est absente. Un proxy qui ne la relaie pas transforme donc un contrôle de
    sécurité en refus systématique — et le client, lui, reconnecte.
    """
    import supervisor

    forwarded = {name.lower() for name in supervisor._WS_FORWARDED_HEADERS}
    assert {"origin", "host", "cookie"} <= forwarded

    built = supervisor._build_ws_proxy_headers(
        {
            "cookie": "jarvis_session=abc",
            "origin": "https://127.0.0.1:9000",
            "host": "127.0.0.1:9000",
        }
    )
    assert built["Origin"] == "https://127.0.0.1:9000"
    assert built["Host"] == "127.0.0.1:9000"
    assert built["Cookie"] == "jarvis_session=abc"


def test_ws_proxy_omits_absent_headers():
    """Un en-tête absent ne doit pas devenir une chaîne vide.

    Une ``Origin: ''`` serait transmise, puis rejetée par la canonicalisation —
    même symptôme qu'avant, pour une raison différente.
    """
    import supervisor

    built = supervisor._build_ws_proxy_headers({"host": "127.0.0.1:9000"})
    assert "Origin" not in built
    assert "Cookie" not in built
    assert built["Host"] == "127.0.0.1:9000"


def test_ws_proxy_does_not_invent_an_origin():
    """Le proxy relaie, il ne fabrique pas.

    Poser une Origin par défaut ferait passer n'importe quelle page pour
    légitime : le contrôle du backend deviendrait décoratif.
    """
    source = inspect.getsource(
        __import__("supervisor")._build_ws_proxy_headers
    )
    for suspicious in ("https://127.0.0.1", "localhost", "default"):
        assert suspicious not in source, (
            f"le proxy semble fabriquer une origine ({suspicious!r})"
        )


# ── 2. Réponse vide du modèle ────────────────────────────────────────────────


def test_empty_response_does_not_blame_comprehension():
    """« Je n'ai pas compris » sur une transcription correcte est un mensonge.

    Le message envoyait reformuler alors que le texte reconnu était juste ; la
    cause réelle — un modèle qui ne rend rien — restait invisible.
    """
    source = (PROJECT_ROOT / "api" / "voice_processing.py").read_text(encoding="utf-8")

    # On vise l'**affectation**, pas la mention : le commentaire cite
    # volontairement l'ancienne phrase pour expliquer pourquoi elle est partie,
    # et interdire la citation reviendrait à interdire de documenter la
    # correction.
    assigned = [
        line.strip()
        for line in source.splitlines()
        if "response_text =" in line and not line.strip().startswith("#")
    ]
    assert not any("Je n'ai pas compris" in line for line in assigned), (
        "la phrase qui accuse la compréhension est de nouveau assignée"
    )
    assert any("Je n'ai pas obtenu de reponse" in line for line in assigned)


def test_empty_response_is_logged_visibly_with_its_cause():
    """La cause doit être journalisée en WARNING, pas en DEBUG.

    Les données du diagnostic existaient déjà dans la trace ; elles
    n'atteignaient simplement jamais le journal.
    """
    source = (PROJECT_ROOT / "api" / "voice_processing.py").read_text(encoding="utf-8")
    block = source[source.index("Reponse vide : dire ce qui"):]
    block = block[: block.index("_persist_voice_messages_async")]

    assert "logger.warning" in block
    assert "logger.debug" not in block
    assert "tokens_out" in block
    assert "empty_response_cause" in block
    # Les deux causes ne se soignent pas pareil : réseau/quota d'un côté,
    # parseur qui retire un tag isolé de l'autre.
    assert "aucun_contenu" in block and "tag_emotion_seul" in block


# ── 3. Disjoncteur Mail ──────────────────────────────────────────────────────


def test_mail_stops_calling_after_repeated_timeouts(monkeypatch):
    """Un osascript expiré ne se répare pas en le rappelant."""
    from integrations import mail as mail_mod

    calls = 0

    class _Result:
        ok = False
        reason = "timeout"
        returncode = -1
        stderr = ""
        stdout = ""

        def is_permission_denied(self):
            return False

        def is_app_not_running(self):
            return False

    def fake_run(script, timeout=None):
        nonlocal calls
        calls += 1
        return _Result()

    monkeypatch.setattr(mail_mod, "run_applescript", fake_run)
    client = mail_mod.AppleMailClient()

    assert client._run_applescript("tell app \"Mail\" to return 1") is None
    first_round = calls
    assert first_round <= 2, "au plus une reprise par appel"

    # Le disjoncteur est ouvert : plus aucun sous-processus n'est lancé.
    assert client._run_applescript("tell app \"Mail\" to return 1") is None
    assert calls == first_round, "un appel a été émis alors que le disjoncteur est ouvert"
    assert client._breaker_open_until > time.time()


def test_mail_recovers_when_the_cooldown_expires(monkeypatch):
    """La suspension est temporaire : Mail relancé doit être repris."""
    from integrations import mail as mail_mod

    class _Ok:
        ok = True
        reason = None
        stdout = "1"

    client = mail_mod.AppleMailClient()
    client._consecutive_timeouts = 5
    client._breaker_open_until = time.time() - 1  # fenêtre écoulée

    monkeypatch.setattr(mail_mod, "run_applescript", lambda *a, **k: _Ok())
    assert client._run_applescript("tell app \"Mail\" to return 1") == "1"
    assert client._consecutive_timeouts == 0, "le compteur doit repartir de zéro"
