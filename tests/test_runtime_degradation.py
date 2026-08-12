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


def test_ws_proxy_declares_the_browser_pair_with_a_token():
    """Relayer ``Host`` ne suffit pas : la bibliothèque cliente le réécrit.

    `websockets/client.py` fait `headers["Host"] = build_host(...)` depuis l'URI
    de connexion, donc toute valeur passée en en-tête additionnel est écrasée en
    silence. Le backend recevait l'Origin du navigateur avec le Host du backend
    et refusait en 403 — indéfiniment. La paire réelle est donc **déclarée**,
    et authentifiée par le jeton privé du superviseur.
    """
    import supervisor

    built = supervisor._build_ws_proxy_headers(
        {
            "cookie": "jarvis_session=abc",
            "origin": "https://127.0.0.1:9000",
            "host": "127.0.0.1:9000",
        }
    )
    assert built["Origin"] == "https://127.0.0.1:9000"
    assert built["Cookie"] == "jarvis_session=abc"
    assert built["X-Forwarded-Origin"] == "https://127.0.0.1:9000"
    assert built["X-Forwarded-Host"] == "127.0.0.1:9000"
    # Sans preuve d'identité, n'importe quel client local pourrait déclarer
    # l'origine de son choix.
    assert "X-Jarvis-Control-Token" in built

    # `Host` n'est pas relayé : le poser donnerait l'illusion d'un contrôle.
    assert "Host" not in built


def test_ws_proxy_declares_nothing_without_a_complete_pair():
    """Une déclaration incomplète ne doit pas être signée.

    Signer un couple partiel reviendrait à demander au backend de faire
    confiance à une valeur manquante.
    """
    import supervisor

    built = supervisor._build_ws_proxy_headers({"host": "127.0.0.1:9000"})
    assert "X-Forwarded-Origin" not in built
    assert "X-Jarvis-Control-Token" not in built


def test_backend_refuses_a_declared_origin_without_token(monkeypatch):
    """Le jeton est la seule chose qui distingue le proxy d'un client local."""
    from api import middleware

    class _WS:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "127.0.0.1"})()

    forged = _WS({
        "X-Forwarded-Origin": "https://evil.example",
        "X-Forwarded-Host": "evil.example",
    })
    assert middleware._proxied_websocket_origin_allowed(forged) is False


def test_backend_refuses_a_declared_pair_that_does_not_match(monkeypatch):
    """La propriété vérifiée reste « origine == hôte visé ».

    Le proxy rapporte les valeurs ; il ne décide pas à la place du backend.
    """
    from api import middleware

    monkeypatch.setattr(
        middleware, "verify_supervisor_control_token", lambda token: True
    )

    class _WS:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "127.0.0.1"})()

    mismatched = _WS({
        middleware.SUPERVISOR_CONTROL_HEADER: "jeton",
        "X-Forwarded-Origin": "https://evil.example",
        "X-Forwarded-Host": "127.0.0.1:9000",
    })
    assert middleware._proxied_websocket_origin_allowed(mismatched) is False


def test_backend_refuses_a_declared_origin_from_a_remote_peer(monkeypatch):
    """Hors boucle locale, la déclaration n'a aucune valeur."""
    from api import middleware

    monkeypatch.setattr(
        middleware, "verify_supervisor_control_token", lambda token: True
    )

    class _WS:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "192.168.1.50"})()

    remote = _WS({
        middleware.SUPERVISOR_CONTROL_HEADER: "jeton",
        "X-Forwarded-Origin": "https://127.0.0.1:9000",
        "X-Forwarded-Host": "127.0.0.1:9000",
    })
    assert middleware._proxied_websocket_origin_allowed(remote) is False


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


def test_chat_ws_origin_check_accepts_the_declared_pair(monkeypatch):
    """C'est `ws_session`, pas `middleware`, qui garde `/ws`.

    Deux contrôles d'origine coexistent : `browser_websocket_origin_allowed`
    protège `/ws/tv/events`, et `_websocket_cookie_origin_allowed` protège le
    chat. Corriger le premier ne corrigeait rien pour le second — cette
    duplication est ce qui a rendu le défaut si long à cerner.
    """
    from starlette.datastructures import Headers

    from api import middleware
    from api.ws_session import _websocket_cookie_origin_allowed

    # Le jeton réel vit à côté de la base, que la suite isole. On double sa
    # vérification : ce test porte sur la logique d'origine, pas sur le stockage
    # du secret — celui-ci est couvert par les tests du plan de contrôle.
    monkeypatch.setattr(
        middleware, "verify_supervisor_control_token", lambda token: bool(token)
    )

    def supervisor_control_headers():
        return {middleware.SUPERVISOR_CONTROL_HEADER: "jeton-de-test"}

    class _WS:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "127.0.0.1"})()
            self.url = type("U", (), {"scheme": "wss"})()

    def _mk(mapping):
        return _WS(
            Headers(raw=[(k.lower().encode(), v.encode()) for k, v in mapping.items()])
        )

    # Le proxy annonce l'origine du navigateur ; le `Host` réel est celui du
    # backend, réécrit par la bibliothèque cliente.
    proxied = {
        "Origin": "https://127.0.0.1:9000",
        "Host": "127.0.0.1:8081",
        "X-Forwarded-Origin": "https://127.0.0.1:9000",
        "X-Forwarded-Host": "127.0.0.1:9000",
    }
    proxied.update(supervisor_control_headers())
    assert _websocket_cookie_origin_allowed(_mk(proxied)) is True

    # Une déclaration incohérente reste refusée : le proxy rapporte, il ne
    # décide pas.
    forged = dict(proxied)
    forged["X-Forwarded-Origin"] = "https://evil.example"
    assert _websocket_cookie_origin_allowed(_mk(forged)) is False

    # Sans jeton, la déclaration ne vaut rien.
    unsigned = {k: v for k, v in proxied.items() if "Token" not in k}
    assert _websocket_cookie_origin_allowed(_mk(unsigned)) is False

    # Le chemin direct, même origine, n'est pas modifié.
    direct = {"Origin": "https://127.0.0.1:8081", "Host": "127.0.0.1:8081"}
    assert _websocket_cookie_origin_allowed(_mk(direct)) is True


# ── 2. Réponse vide du modèle ────────────────────────────────────────────────


def test_empty_response_does_not_blame_comprehension():
    """« Je n'ai pas compris » sur une transcription correcte est un mensonge.

    Le message envoyait reformuler alors que le texte reconnu était juste ; la
    cause réelle — un modèle qui ne rend rien — restait invisible.
    """
    # L'unification du moteur vocal a déplacé le repli : l'adaptateur vocal
    # pose encore le sien, mais le tour passe désormais par le moteur canonique,
    # qui substituait « Bien noté. » à une réponse vide — un mensonge pire que
    # le précédent, puisqu'il prétend avoir enregistré la demande. Les deux
    # sites sont donc vérifiés ensemble.
    sources = {
        name: (PROJECT_ROOT / "api" / name).read_text(encoding="utf-8")
        for name in ("voice_processing.py", "chat_processing.py")
    }

    # On vise l'**affectation**, pas la mention : le commentaire cite
    # volontairement l'ancienne phrase pour expliquer pourquoi elle est partie,
    # et interdire la citation reviendrait à interdire de documenter la
    # correction.
    for name, source in sources.items():
        assigned = [
            line.strip()
            for line in source.splitlines()
            if ("response_text =" in line or "display_text =" in line)
            and not line.strip().startswith("#")
        ]
        assert not any("Je n'ai pas compris" in line for line in assigned), (
            f"{name} : la phrase qui accuse la compréhension est de nouveau assignée"
        )
        assert not any('= "Bien noté."' in line for line in assigned), (
            f"{name} : « Bien noté. » prétend avoir enregistré une demande "
            "que le modèle n'a jamais traitée"
        )

    assert any(
        "Je n'ai pas obtenu de reponse" in line
        for source in sources.values()
        for line in source.splitlines()
        if not line.strip().startswith("#")
    )


def test_empty_response_is_logged_visibly_with_its_cause():
    """La cause doit être journalisée en WARNING, pas en DEBUG.

    Les données du diagnostic existaient déjà dans la trace ; elles
    n'atteignaient simplement jamais le journal.
    """
    # Le diagnostic vit maintenant dans le moteur canonique : c'est le seul
    # endroit qui voit la réponse brute du modèle et ses jetons. L'adaptateur
    # vocal, lui, ne reçoit qu'un texte déjà nettoyé — il ne peut plus
    # distinguer « rien du tout » de « seulement le tag [emotion] ».
    source = (PROJECT_ROOT / "api" / "chat_processing.py").read_text(encoding="utf-8")
    block = source[source.index('cause = "aucun_contenu"') :]
    block = block[: block.index("if persist_assistant")]

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
