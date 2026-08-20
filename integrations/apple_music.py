"""Apple Music comme outil JARVIS — binaire MCP local, pas une mission agentique.

# ponytail: library search only, catalog_search when apple-music-mcp exposes it
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_BINARY_NAME = "apple-music-mcp"
_STATUS_TTL_S = 10.0
_DOCTOR_TIMEOUT_S = 2.5
_MCP_INIT_TIMEOUT_S = 3.0
_MCP_CALL_TIMEOUT_S = 15.0
_VOLUME_STEP = 10

_ERROR_BINARY_MISSING = "binary_missing"
_ERROR_DOCTOR_FAILED = "doctor_failed"
_ERROR_AUTOMATION_DENIED = "automation_denied"
_ERROR_NOT_IN_LIBRARY = "not_in_library"
_ERROR_UNAVAILABLE = "unavailable"

_MUSIC_HINT = re.compile(
    r"\b(musique|apple music|morceau|chanson|album|artiste|playlist)\b",
    re.IGNORECASE,
)
_BLOCKED_QUERY = re.compile(
    r"^(?:la |le |les |cette |ce )?"
    r"(?:tache|tâche|tests?|commandes?|git|pr|pull request|build|ci)\b",
    re.IGNORECASE,
)
_PLAY_RE = re.compile(
    r"^(?:jarvis )?(?:peux tu )?(?:mets?|joue|play|lance)(?: moi)?"
    r"(?: (?:du|de la|de l|de|la|le|les|some))?"
    r"(?: (?:musique(?: de| d)?|morceau|chanson|album|artiste))?"
    r"(?: de)?"
    r" (.+?)(?: sur apple music| sur music| dans apple music)?$",
    re.IGNORECASE,
)
_VOLUME_ABS_RE = re.compile(
    r"^(?:mets?(?: le)? volume(?: a| à)?|volume) (\d{1,3})$",
    re.IGNORECASE,
)

_status_cache: tuple[float, dict[str, Any]] | None = None


class AppleMusicError(Exception):
    """Échec MCP ou binaire — ``code`` est un identifiant fermé."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def resolve_binary() -> str | None:
    """Trouve ``apple-music-mcp`` même si le PATH d'une app macOS est réduit."""

    candidate = shutil.which(_BINARY_NAME)
    if candidate is None:
        local_binary = Path.home() / ".local" / "bin" / _BINARY_NAME
        if local_binary.is_file() and os.access(local_binary, os.X_OK):
            candidate = str(local_binary)
    return str(Path(candidate).resolve()) if candidate else None


def reset_status_cache() -> None:
    global _status_cache
    _status_cache = None


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _strip_paths(text: str) -> str:
    return re.sub(r"(?:/|\b[A-Za-z]:\\)[^\s]+", "[path]", text)


def _parse_doctor(stdout: str) -> dict[str, Any]:
    granted = bool(re.search(r"Automation:\s*GRANTED", stdout, re.IGNORECASE))
    denied = bool(re.search(r"Automation:\s*(DENIED|NOT GRANTED|MISSING)", stdout, re.I))
    complete = "Doctor check complete" in stdout or "Doctor check" in stdout
    backend = "musicapp" if "musicapp" in stdout.lower() or "Music.app" in stdout else None
    if granted and complete:
        return {
            "state": "healthy",
            "healthy": True,
            "running": True,
            "error": None,
            "backend": backend or "musicapp",
        }
    if denied or (complete and not granted and "Automation:" in stdout):
        return {
            "state": "degraded",
            "healthy": False,
            "running": False,
            "error": _ERROR_AUTOMATION_DENIED,
            "backend": backend or "musicapp",
        }
    return {
        "state": "unavailable",
        "healthy": False,
        "running": False,
        "error": _ERROR_DOCTOR_FAILED,
        "backend": backend,
    }


def status(*, force: bool = False) -> dict[str, Any]:
    """Sonde non destructive. Jamais de chemin dans le dict public."""

    global _status_cache
    now = time.monotonic()
    if not force and _status_cache is not None and now - _status_cache[0] < _STATUS_TTL_S:
        return dict(_status_cache[1])

    binary = resolve_binary()
    if binary is None:
        payload = {
            "state": "unknown",
            "healthy": False,
            "running": False,
            "available": False,
            "error": _ERROR_BINARY_MISSING,
            "backend": None,
        }
        _status_cache = (now, payload)
        return dict(payload)

    try:
        completed = subprocess.run(
            [binary, "doctor"],
            capture_output=True,
            text=True,
            timeout=_DOCTOR_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("[apple_music] doctor impossible")
        payload = {
            "state": "unknown",
            "healthy": False,
            "running": False,
            "available": True,
            "error": _ERROR_DOCTOR_FAILED,
            "backend": None,
        }
        _status_cache = (now, payload)
        return dict(payload)

    parsed = _parse_doctor(_strip_paths(completed.stdout or ""))
    payload = {
        **parsed,
        "available": True,
    }
    _status_cache = (now, payload)
    return dict(payload)


def control_payload() -> dict[str, Any]:
    """Carte Control Center — ``can_control`` faux : ce n'est pas un daemon."""

    probe = status()
    return {
        "id": "apple_music",
        "name": "Apple Music MCP",
        "description": "Music.app via MCP local (lecture, pas une tâche)",
        "category": "integrations",
        "running": bool(probe.get("running")),
        "healthy": bool(probe.get("healthy")),
        "state": probe.get("state") or "unknown",
        "can_control": False,
        "error": probe.get("error"),
    }


def integrations_payload() -> dict[str, Any]:
    probe = status()
    return {
        "available": bool(probe.get("available")),
        "healthy": bool(probe.get("healthy")),
        "backend": probe.get("backend"),
        "error": probe.get("error"),
    }


def _send(proc: subprocess.Popen[bytes], payload: Mapping[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
    proc.stdin.flush()


def _recv(proc: subprocess.Popen[bytes], timeout: float) -> dict[str, Any]:
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_timeout")
    line = proc.stdout.readline()
    if not line:
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_eof")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_invalid_json") from exc
    if not isinstance(message, dict):
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_invalid_message")
    return message


def _unwrap_tool(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("error"):
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_rpc_error")
    result = message.get("result")
    if not isinstance(result, dict):
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_empty_result")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return {"ok": False, "error": {"code": _ERROR_UNAVAILABLE}}
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else None
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": {"code": _ERROR_UNAVAILABLE}}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_tool_json") from exc
    if not isinstance(parsed, dict):
        raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_tool_shape")
    return parsed


class _McpSession:
    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1

    def __enter__(self) -> _McpSession:
        self._proc = subprocess.Popen(
            [self._binary, "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            _send(
                self._proc,
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "jarvis", "version": "1"},
                    },
                },
            )
            self._next_id += 1
            _recv(self._proc, _MCP_INIT_TIMEOUT_S)
        except Exception:
            self.close()
            raise
        return self

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._proc is None:
            raise AppleMusicError(_ERROR_UNAVAILABLE, "mcp_closed")
        request_id = self._next_id
        self._next_id += 1
        _send(
            self._proc,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments or {})},
            },
        )
        return _unwrap_tool(_recv(self._proc, _MCP_CALL_TIMEOUT_S))

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            logger.debug("[apple_music] arrêt MCP ignoré", exc_info=True)

    def __exit__(self, *exc: object) -> None:
        self.close()


def _with_session() -> _McpSession:
    binary = resolve_binary()
    if binary is None:
        raise AppleMusicError(_ERROR_BINARY_MISSING)
    return _McpSession(binary)


def _pick_artist(query: str, artists: list[dict[str, Any]]) -> str | None:
    folded = _fold(query)
    names = [
        str(item.get("name") or "").strip()
        for item in artists
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    exact = [name for name in names if _fold(name) == folded]
    if exact:
        exact.sort(key=len)
        return exact[0]
    starts = [name for name in names if _fold(name).startswith(folded)]
    if starts:
        starts.sort(key=len)
        return starts[0]
    return None


def _track_label(track: Mapping[str, Any]) -> str:
    title = str(track.get("name") or "").strip() or "Piste"
    artist = str(track.get("artist") or "").strip()
    return f"{artist} — {title}" if artist else title


def play_query(query: str) -> dict[str, Any]:
    """Cherche dans la bibliothèque locale puis lance lecture artiste ou piste."""

    cleaned = query.strip()
    if not cleaned:
        return _playback({"action": "play"})
    with _with_session() as session:
        artists_payload = session.call(
            "music_search",
            {"query": cleaned, "limit": 10, "types": ["artist"]},
        )
        artists = []
        if artists_payload.get("ok"):
            data = artists_payload.get("data") or {}
            if isinstance(data, dict):
                raw = data.get("artists") or []
                if isinstance(raw, list):
                    artists = [item for item in raw if isinstance(item, dict)]
        artist = _pick_artist(cleaned, artists)
        if artist:
            played = session.call(
                "music_playback",
                {
                    "action": "play_artist",
                    "target_id": artist,
                    "target_type": "artist",
                },
            )
            if played.get("ok"):
                return {
                    "ok": True,
                    "message": f"{artist}, lecture lancée.",
                    "artist": artist,
                    "query": cleaned,
                }
        tracks_payload = session.call(
            "music_search",
            {"query": cleaned, "limit": 10, "types": ["track"]},
        )
        tracks: list[dict[str, Any]] = []
        if tracks_payload.get("ok"):
            data = tracks_payload.get("data") or {}
            if isinstance(data, dict):
                raw = data.get("tracks") or []
                if isinstance(raw, list):
                    tracks = [item for item in raw if isinstance(item, dict)]
        if not tracks:
            return {
                "ok": False,
                "error": _ERROR_NOT_IN_LIBRARY,
                "message": (
                    f"Rien pour {cleaned} dans la bibliothèque Music.app. "
                    "La recherche catalogue n'est pas disponible."
                ),
                "query": cleaned,
            }
        track = tracks[0]
        track_id = str(track.get("persistent_id") or "").strip()
        if not track_id:
            return {
                "ok": False,
                "error": _ERROR_UNAVAILABLE,
                "message": "Piste trouvée sans identifiant lisible.",
                "query": cleaned,
            }
        played = session.call(
            "music_playback",
            {
                "action": "play_track",
                "target_id": track_id,
                "target_type": "track",
            },
        )
        if not played.get("ok"):
            return {
                "ok": False,
                "error": _ERROR_UNAVAILABLE,
                "message": "Lecture refusée par Music.app.",
                "query": cleaned,
            }
        return {
            "ok": True,
            "message": f"{_track_label(track)}, lecture lancée.",
            "artist": track.get("artist"),
            "track": track.get("name"),
            "query": cleaned,
        }


def _playback(arguments: Mapping[str, Any]) -> dict[str, Any]:
    with _with_session() as session:
        result = session.call("music_playback", arguments)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": _ERROR_UNAVAILABLE,
            "message": "Music.app n'a pas exécuté la commande.",
        }
    action = str(arguments.get("action") or "play")
    labels = {
        "play": "Lecture reprise.",
        "pause": "Lecture en pause.",
        "next": "Piste suivante.",
        "previous": "Piste précédente.",
        "stop": "Lecture arrêtée.",
    }
    return {"ok": True, "message": labels.get(action, "Commande envoyée.")}


def get_state() -> dict[str, Any]:
    with _with_session() as session:
        result = session.call("music_get_state", {})
    if not result.get("ok"):
        return {"ok": False, "error": _ERROR_UNAVAILABLE, "message": "État illisible."}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    track = data.get("track") if isinstance(data.get("track"), dict) else {}
    artist = str(track.get("artist") or data.get("artist") or "").strip()
    title = str(track.get("name") or data.get("name") or "").strip()
    player_state = str(data.get("player_state") or "").strip()
    volume = data.get("volume")
    parts = []
    if artist or title:
        parts.append(_track_label({"name": title or "Piste", "artist": artist}))
    if player_state:
        parts.append(player_state)
    if isinstance(volume, (int, float)):
        parts.append(f"volume {int(volume)}")
    return {
        "ok": True,
        "message": ", ".join(parts) if parts else "Aucune piste en lecture.",
        "artist": artist or None,
        "track": title or None,
        "player_state": player_state or None,
        "volume": int(volume) if isinstance(volume, (int, float)) else None,
    }


def set_volume(volume: int) -> dict[str, Any]:
    clamped = max(0, min(100, int(volume)))
    with _with_session() as session:
        result = session.call(
            "music_preferences",
            {"action": "set_volume", "volume": clamped},
        )
    if not result.get("ok"):
        return {"ok": False, "error": _ERROR_UNAVAILABLE, "message": "Volume inchangé."}
    return {"ok": True, "message": f"Volume {clamped}.", "volume": clamped}


def context_line() -> str | None:
    """Une ligne d'état lecture, sans chemin. ``None`` si le MCP est absent."""

    if resolve_binary() is None:
        return None
    try:
        state = get_state()
    except AppleMusicError:
        return None
    if not state.get("ok"):
        return None
    message = str(state.get("message") or "").strip()
    return f"Lecture : {message}" if message else None


@dataclass(frozen=True)
class MusicIntent:
    action: str
    query: str = ""
    volume: int | None = None
    committed: bool = True


def parse_intent(text: str) -> MusicIntent | None:
    """None = pas un ordre musique. ``committed=False`` : fallthrough si lib vide."""

    folded = _fold(text)
    if not folded:
        return None
    if folded in {"execute", "approuve", "accepte", "valide", "autorise", "refuse"}:
        return None
    if re.search(r"\bpause la tache\b", folded) or re.search(
        r"\b(annule|arrete)(?: la| cette)? tache\b", folded
    ):
        return None
    if folded in {"lance", "lance la tache", "execute le plan"}:
        return None

    if folded in {
        "mets de la musique",
        "met de la musique",
        "joue de la musique",
        "joue la musique",
        "lance la musique",
    }:
        return MusicIntent("play", committed=True)

    if folded in {"pause", "pause la musique", "mets en pause", "met en pause"}:
        return MusicIntent("pause")
    if folded in {"suivant", "next", "piste suivante", "morceau suivant"}:
        return MusicIntent("next")
    if folded in {"precedent", "previous", "piste precedente", "morceau precedent"}:
        return MusicIntent("previous")
    if folded in {"stop", "arrete la musique", "stoppe la musique"}:
        return MusicIntent("stop")
    if folded in {"quoi tu joues", "quelle musique", "etat de la musique", "what s playing"}:
        return MusicIntent("state")

    volume_abs = _VOLUME_ABS_RE.match(folded)
    if volume_abs:
        return MusicIntent("set_volume", volume=max(0, min(100, int(volume_abs.group(1)))))
    if folded in {"monte le son", "plus fort", "augmente le volume"}:
        return MusicIntent("set_volume", volume=-1)
    if folded in {"baisse le son", "moins fort", "baisse le volume"}:
        return MusicIntent("set_volume", volume=-2)

    play = _PLAY_RE.match(folded)
    if play is None:
        return None
    query = play.group(1).strip()
    verb = folded.split(" ", 1)[0]
    if query in {"musique", "la musique", "apple music", "music"}:
        return MusicIntent("play", committed=True)
    if _BLOCKED_QUERY.match(query):
        return None
    if len(query) < 2:
        return None
    committed = verb in {"joue", "play"} or bool(_MUSIC_HINT.search(text))
    return MusicIntent("play", query=query, committed=committed)


def _unavailable_response(message: str, action: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": message,
        "emotion": "neutral",
        "action": action,
        "action_result": {"ok": False, "message": message},
        "agent": "info",
        "model": "runtime",
        "cost": 0.0,
    }


def _ok_response(result: Mapping[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    text = str(result.get("message") or "").strip() or "C'est fait."
    return {
        "text": text,
        "emotion": "neutral",
        "action": action,
        "action_result": dict(result),
        "agent": "info",
        "model": "runtime",
        "cost": 0.0,
    }


def execute_music_action(action: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch ``type=music`` — allowlist d'actions, jamais de playlist destructive."""

    raw = str(action.get("action") or "").strip().lower()
    if raw not in {
        "play",
        "pause",
        "next",
        "previous",
        "stop",
        "state",
        "set_volume",
    }:
        return {"ok": False, "message": f"Action musique inconnue : {raw or '∅'}"}
    try:
        if raw == "play":
            query = str(action.get("query") or "").strip()
            return play_query(query)
        if raw == "state":
            return get_state()
        if raw == "set_volume":
            volume = action.get("volume")
            if not isinstance(volume, (int, float)) or isinstance(volume, bool):
                return {"ok": False, "message": "Volume entier 0-100 requis."}
            return set_volume(int(volume))
        playback_name = {
            "pause": "pause",
            "next": "next",
            "previous": "previous",
            "stop": "stop",
        }[raw]
        return _playback({"action": playback_name})
    except AppleMusicError as exc:
        if exc.code == _ERROR_BINARY_MISSING:
            return {
                "ok": False,
                "error": exc.code,
                "message": "Apple Music MCP n'est pas installé sur cette machine.",
            }
        return {
            "ok": False,
            "error": exc.code,
            "message": "Apple Music est indisponible pour le moment.",
        }


async def maybe_handle_music_intent(text: str) -> dict[str, Any] | None:
    """Fast-path chat/voix. ``None`` = laisser le pipeline normal continuer."""

    intent = parse_intent(text)
    if intent is None:
        return None

    action: dict[str, Any] = {"type": "music", "action": intent.action}
    if intent.query:
        action["query"] = intent.query

    if resolve_binary() is None:
        if not intent.committed:
            return None
        return _unavailable_response(
            "Apple Music MCP n'est pas installé sur cette machine.",
            action,
        )

    if intent.action == "set_volume" and intent.volume is not None:
        if intent.volume < 0:
            try:
                state = await asyncio.to_thread(get_state)
            except AppleMusicError:
                return _unavailable_response(
                    "Apple Music est indisponible pour le moment.",
                    action,
                )
            current = state.get("volume")
            base = int(current) if isinstance(current, int) else 50
            target = base + _VOLUME_STEP if intent.volume == -1 else base - _VOLUME_STEP
            action["volume"] = max(0, min(100, target))
        else:
            action["volume"] = intent.volume

    try:
        result = await asyncio.to_thread(execute_music_action, action)
    except AppleMusicError as exc:
        if not intent.committed and exc.code == _ERROR_BINARY_MISSING:
            return None
        return _unavailable_response(
            "Apple Music est indisponible pour le moment.",
            action,
        )

    if (
        not result.get("ok")
        and result.get("error") == _ERROR_NOT_IN_LIBRARY
        and not intent.committed
    ):
        return None
    if result.get("ok"):
        return _ok_response(result, action)
    return _unavailable_response(
        str(result.get("message") or "Apple Music n'a pas pu le faire."),
        action,
    )


async def maybe_music_context(text: str) -> str | None:
    """Injecte l'état lecture seulement si le tour parle de musique."""

    folded = _fold(text)
    if not _MUSIC_HINT.search(folded) and parse_intent(text) is None:
        return None
    if resolve_binary() is None:
        return None
    try:
        return await asyncio.to_thread(context_line)
    except AppleMusicError:
        return None
