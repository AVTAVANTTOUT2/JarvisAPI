"""Interface mobile autonome : montage, redirection, isolation.

Trois propriétés vérifiées ici :
1. un téléphone atterrit sur ``/mobile/``, un bureau jamais ;
2. ``/mobile/`` sert bien des fichiers statiques, et rien d'autre ;
3. ``web_mobile/`` ne dépend d'aucun des arbres frontend existants.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import frontend, web_mobile
from api.frontend import _setup_frontend

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_MOBILE = REPO_ROOT / "web_mobile"

IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
ANDROID_PHONE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"
)
ANDROID_TABLET = (
    "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
MAC_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
IPAD = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Serveur complet avec un frontend bureau minimal.

    Le build bureau est fabriqué ici plutôt que lu sur le disque : en CI
    `frontend/out` n'existe pas, et des tests qui dépendraient d'un artefact
    de build passeraient en local pour échouer sur une machine propre.
    """
    unified = tmp_path / "frontend-out"
    _write(unified / "index.html", "desktop-root")
    _write(unified / "chat" / "index.html", "desktop-chat")
    _write(unified / "_next" / "static" / "app.js", "asset")
    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    monkeypatch.setattr(frontend, "WEB_DIST", tmp_path / "web-dist-absent")

    app = FastAPI()
    _setup_frontend(app)
    return TestClient(app)


# ── Détection ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user_agent", [IPHONE, ANDROID_PHONE])
def test_phones_are_detected(user_agent):
    assert web_mobile.is_mobile_device(user_agent) is True


@pytest.mark.parametrize("user_agent", [MAC_DESKTOP, IPAD, ANDROID_TABLET, ""])
def test_large_screens_are_not_phones(user_agent):
    """Tablettes et iPad restent sur le bureau : l'écran est assez large."""
    assert web_mobile.is_mobile_device(user_agent) is False


# ── Redirection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("user_agent", [IPHONE, ANDROID_PHONE])
def test_phone_is_redirected_from_root(client, user_agent):
    response = client.get("/", headers={"user-agent": user_agent}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/mobile/"


@pytest.mark.parametrize("user_agent", [MAC_DESKTOP, IPAD, ANDROID_TABLET])
def test_desktop_is_never_redirected(client, user_agent):
    response = client.get("/", headers={"user-agent": user_agent}, follow_redirects=False)
    assert response.status_code == 200


def test_desktop_escape_hatch_sets_a_durable_cookie(client):
    """Sans échappatoire, le bureau deviendrait inatteignable depuis un téléphone."""
    response = client.get(
        "/?desktop=1", headers={"user-agent": IPHONE}, follow_redirects=False
    )
    assert response.status_code == 200
    assert web_mobile.FORCE_DESKTOP_COOKIE in response.headers.get("set-cookie", "")


def test_desktop_cookie_alone_disables_the_redirect(client):
    response = client.get(
        "/",
        headers={"user-agent": IPHONE},
        cookies={web_mobile.FORCE_DESKTOP_COOKIE: "1"},
        follow_redirects=False,
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path,target",
    [
        ("/chat", "/mobile/#/chat"),
        ("/dashboard", "/mobile/#/aujourdhui"),
        ("/fitness", "/mobile/#/sante"),
    ],
)
def test_installed_desktop_pwa_entrypoints_are_migrated(client, path, target):
    """Les manifests historiques contournaient `/` via ces deux start_url."""
    response = client.get(path, headers={"user-agent": IPHONE}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == target


@pytest.mark.parametrize(
    "path,target",
    [
        ("/m", "/mobile/"),
        ("/m/", "/mobile/"),
        ("/m/fitness", "/mobile/#/sante"),
        ("/m/fitness/", "/mobile/#/sante"),
    ],
)
def test_historical_mobile_pwa_urls_redirect_to_the_autonomous_app(client, path, target):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == target


def test_other_deep_desktop_links_remain_accessible(client):
    response = client.get("/tasks", headers={"user-agent": IPHONE}, follow_redirects=False)
    assert response.status_code == 200


def test_desktop_escape_hatch_also_works_on_a_legacy_entrypoint(client):
    response = client.get(
        "/chat?desktop=1", headers={"user-agent": IPHONE}, follow_redirects=False
    )
    assert response.status_code == 200
    assert response.text == "desktop-chat"
    assert web_mobile.FORCE_DESKTOP_COOKIE in response.headers.get("set-cookie", "")


# ── Service des fichiers ─────────────────────────────────────────────────

def test_index_is_served(client):
    response = client.get("/mobile/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_bare_prefix_redirects_to_the_directory(client):
    response = client.get("/mobile", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/mobile/"


@pytest.mark.parametrize(
    "path,expected_type",
    [
        ("/mobile/app.css", "text/css"),
        ("/mobile/js/app.js", "application/javascript"),
        ("/mobile/js/views/chat.js", "application/javascript"),
        ("/mobile/js/views/health.js", "application/javascript"),
        ("/mobile/manifest.webmanifest", "application/manifest+json"),
        ("/mobile/icons/icon-192.png", "image/png"),
    ],
)
def test_assets_are_served_with_the_right_type(client, path, expected_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(expected_type)
    assert response.headers["cache-control"] == "no-cache"


def test_missing_file_is_a_plain_404(client):
    """Le routage est par fragment : aucune sous-route ne doit servir l'index."""
    response = client.get("/mobile/absent.js")
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/mobile/../config.py", "/mobile/../../etc/passwd"])
def test_directory_traversal_is_refused(client, path):
    assert client.get(path).status_code == 404


def test_unknown_extensions_are_refused(client, tmp_path, monkeypatch):
    """Le répertoire ne doit exposer que du statique connu."""
    secret = WEB_MOBILE / "note.txt"
    secret.write_text("contenu", encoding="utf-8")
    try:
        assert client.get("/mobile/note.txt").status_code == 404
    finally:
        secret.unlink()


def test_absent_directory_disables_everything(tmp_path, monkeypatch):
    """Sans web_mobile/, pas de redirection et surtout pas de crash."""
    unified = tmp_path / "frontend-out"
    _write(unified / "index.html", "desktop-root")
    _write(unified / "_next" / "static" / "app.js", "asset")
    monkeypatch.setattr(frontend, "FRONTEND_DIST", unified)
    monkeypatch.setattr(frontend, "WEB_DIST", tmp_path / "web-dist-absent")
    monkeypatch.setattr(web_mobile, "WEB_MOBILE_DIR", tmp_path / "vide")

    app = FastAPI()
    _setup_frontend(app)
    client = TestClient(app)
    assert client.get("/mobile").status_code == 404
    assert client.get("/mobile/").status_code == 404
    assert client.get("/", headers={"user-agent": IPHONE}, follow_redirects=False).status_code == 200


def test_missing_mobile_bundle_never_falls_back_to_a_desktop_shell(
    tmp_path,
    monkeypatch,
):
    """`/mobile` reste réservé, sous Next comme sous le repli Vite."""
    unified = tmp_path / "frontend-out"
    _write(unified / "index.html", "unified-desktop")
    _write(unified / "_next" / "static" / "app.js", "asset")
    vite = tmp_path / "web-dist"
    _write(vite / "index.html", "vite-desktop")
    monkeypatch.setattr(web_mobile, "WEB_MOBILE_DIR", tmp_path / "mobile-absent")

    configurations = (
        (unified, tmp_path / "vite-absent"),
        (tmp_path / "unified-absent", vite),
    )
    for unified_root, vite_root in configurations:
        monkeypatch.setattr(frontend, "FRONTEND_DIST", unified_root)
        monkeypatch.setattr(frontend, "WEB_DIST", vite_root)
        app = FastAPI()
        _setup_frontend(app)
        with TestClient(app) as client:
            assert client.get("/mobile").status_code == 404
            assert client.get("/mobile/anything").status_code == 404

    assert "mobile" not in frontend._SPA_SEGMENTS
    assert "mobile" not in frontend._UNIFIED_SEGMENTS


def test_mobile_survives_a_missing_desktop_build(tmp_path, monkeypatch):
    """L'interface mobile est autonome : elle ne dépend d'aucun build React.

    Sans cette garantie, un téléphone recevrait 404 sur une machine où le
    frontend n'a pas encore été construit — exactement le cas de la CI.
    """
    monkeypatch.setattr(frontend, "FRONTEND_DIST", tmp_path / "frontend-absent")
    monkeypatch.setattr(frontend, "WEB_DIST", tmp_path / "web-absent")
    monkeypatch.setattr(frontend, "WEB_TEMPLATES", tmp_path / "templates-absent")

    app = FastAPI()
    _setup_frontend(app)
    client = TestClient(app)

    response = client.get("/", headers={"user-agent": IPHONE}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/mobile/"
    assert client.get("/mobile/").status_code == 200
    # Le bureau, lui, annonce franchement qu'il manque un build.
    assert client.get("/", headers={"user-agent": MAC_DESKTOP}).status_code == 503


# ── Isolation ────────────────────────────────────────────────────────────

def _sources() -> list[Path]:
    return [
        p for p in WEB_MOBILE.rglob("*")
        if p.is_file() and p.suffix in {".js", ".css", ".html", ".webmanifest"}
    ]


def test_no_import_from_the_existing_frontends():
    """C2 : casser le mobile ne doit jamais pouvoir casser le bureau."""
    forbidden = ("web/src", "pwa/src", "frontend/src", "jarvis_auth", "@jarvis/auth",
                 "@desktop/", "@mobile/", "@unified/", "@frontend/")
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {needle}")
    assert offenders == []


def test_no_build_toolchain():
    """C1 : aucun build, donc aucun manifeste de paquets ni dépendance installée."""
    for name in ("package.json", "package-lock.json", "pnpm-lock.yaml", "node_modules", "tsconfig.json"):
        assert not (WEB_MOBILE / name).exists(), f"{name} ne doit pas exister dans web_mobile/"


def test_no_remote_resources():
    """C6 : la CSP est `default-src 'self'` — tout doit être local.

    Les URI d'espace de noms XML (``http://www.w3.org/2000/svg``) sont des
    identifiants, jamais chargés : les exclure évite un faux positif.
    """
    namespaces = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink")
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for ns in namespaces:
            text = text.replace(ns, "")
        for needle in ("https://", "http://", "//cdn.", "fonts.googleapis"):
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {needle}")
    assert offenders == []


def test_input_fields_never_trigger_ios_zoom():
    """Safari zoome au focus dès que la police du champ passe sous 16 px."""
    css = (WEB_MOBILE / "app.css").read_text(encoding="utf-8")
    assert "font-size: 16px" in css


def test_safe_areas_are_honoured():
    css = (WEB_MOBILE / "app.css").read_text(encoding="utf-8")
    assert "safe-area-inset-top" in css
    assert "safe-area-inset-bottom" in css


# ── Contrats client ↔ API / WebSocket ─────────────────────────────────────

def _mobile_source(relative_path: str) -> str:
    return (WEB_MOBILE / relative_path).read_text(encoding="utf-8")


def test_action_refusal_uses_the_server_cancel_protocol():
    """Les deux décisions ne renvoient que l'identifiant opaque du serveur."""
    chat = _mobile_source("js/views/chat.js")
    websocket = _mobile_source("js/ws.js")
    assert "ws.cancelAction(action)" in chat
    assert "type: 'action_cancel'" in websocket
    assert "type: 'action_confirm'" in websocket
    assert "proposal_id: proposal?.proposal_id" in websocket
    assert "action_confirm', action" not in websocket
    assert "confirmed: false" not in chat


def test_today_calendar_supplies_the_required_date_range():
    """GET /api/calendar renvoie 400 sans `start` et `end`."""
    api_source = _mobile_source("js/api.js")
    today_source = _mobile_source("js/views/today.js")
    assert "/api/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}" in api_source
    assert "api.calendar(localIso(dayStart), localIso(dayEnd))" in today_source


def test_chat_restores_and_switches_conversation_history():
    api_source = _mobile_source("js/api.js")
    chat = _mobile_source("js/views/chat.js")
    assert "/api/conversations?limit=${n}" in api_source
    assert "/api/conversations/${id}" in api_source
    assert "ws.currentConversationId()" in chat
    assert "ws.on('conversation_switched'" in chat


def test_voice_always_acknowledges_a_tts_turn():
    """Même sans octet audio, done_playing libère l'état PTT du serveur."""
    voice = _mobile_source("js/views/voice.js")
    empty_branch = voice.index("if (!speech.length)")
    finish_call = voice.index("finishPlayback();", empty_branch)
    done_call = voice.index("ws.donePlaying();")
    assert empty_branch < finish_call
    assert done_call < empty_branch


def test_idle_timer_treats_background_time_as_inactivity():
    auth_source = _mobile_source("js/auth.js")
    assert "document.hidden" in auth_source
    assert "Date.now() - lastActivity" in auth_source
    assert "visibilitychange', visibility" in auth_source


def test_mobile_lock_accepts_variable_length_pin():
    """Le bureau accepte un PIN à 4 chiffres ; le pavé mobile ne doit pas exiger 6."""
    auth_source = _mobile_source("js/auth.js")
    assert "const MIN_PIN = 4" in auth_source
    assert "const MAX_PIN = 12" in auth_source
    assert 'data-action="ok"' in auth_source or "dataset.action = 'ok'" in auth_source
    # Ne plus auto-soumettre uniquement à une longueur fixe de 6.
    assert "const LEN = 6" not in auth_source
    assert "code.length === MAX_PIN" in auth_source


def test_mobile_auth_supports_passphrases_and_persists_idle_lock():
    auth_source = _mobile_source("js/auth.js")
    api_source = _mobile_source("js/api.js")
    index_source = _mobile_source("index.html")
    assert "const MIN_PASSPHRASE = 10" in auth_source
    assert "data-secret-kind=\"passphrase\"" in index_source
    assert "lock-passphrase-submit" in index_source
    assert "localStorage.setItem(SOFT_LOCK_KEY, '1')" in auth_source
    assert "st.authenticated && !hasPersistedSoftLock()" in auth_source
    assert "const result = await api.verify(entered)" in auth_source
    assert "'/api/auth/verify'" in api_source


def test_mobile_shell_exposes_a_real_logout_action():
    app_source = _mobile_source("js/app.js")
    assert "label: 'Se déconnecter'" in app_source
    assert "await api.logout()" in app_source


def test_user_chat_never_renders_internal_agent_names():
    chat_source = (REPO_ROOT / "web/src/app/components/views/ChatView.tsx").read_text(
        encoding="utf-8"
    )
    assert "message.agent &&" not in chat_source


def test_mobile_fitness_matches_the_connected_desktop_experience():
    """La PWA autonome doit conserver les capacités de la nouvelle vue desktop."""
    health = _mobile_source("js/views/health.js")
    for contract in (
        "current_streak_weeks",
        "latest_weight",
        "/api/fitness/weights",
        "/api/fitness/program",
        "/api/fitness/program/sessions/",
        "setProgress('planned', [])",
        "Objectifs et rappels",
        "Modifier la séance",
        "exercise.sides === 2",
        "À la sensation",
        "meal.calories_estimate ?? '?'",
        "Analyser (IA)",
        "createMealFromText",
        "Analyse alimentaire impossible.",
        "createMealFromPhoto",
        "Analyser la photo (IA)",
        "capture: 'environment'",
        "createWellbeing",
        "Enregistrer mon ressenti",
        "journal_text: journal.value.trim() || null",
    ):
        assert contract in health


def test_mobile_fitness_uses_safe_ratios_and_explicit_local_dates():
    health = _mobile_source("js/views/health.js")
    assert "if (!target) return 0;" in health
    assert "/api/fitness/dashboard?date=${encodeURIComponent(localIsoDate())}" in health
    assert "/api/fitness/advice?date=${encodeURIComponent(dashboard.date)}" in health


def test_mobile_fitness_validates_program_settings_before_patch():
    health = _mobile_source("js/views/health.js")
    assert "validateProgramSettings(fields, reminderTime)" in health
    assert "if (!raw) return { error:" in health
    assert "payload[key] = Number(node.value)" not in health
    assert "payload.calories_min > payload.calories_max" in health


def test_mobile_fitness_assets_are_cache_busted_from_the_shell():
    index = _mobile_source("index.html")
    app = _mobile_source("js/app.js")
    assert "/mobile/app.css?v=20260804" in index
    assert "/mobile/js/app.js?v=20260804" in index
    assert "./views/health.js?v=20260804" in app
