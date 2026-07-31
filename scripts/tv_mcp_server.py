#!/usr/bin/env python3
"""
TV Browser MCP Server — Contrôle CDP du navigateur Kiwi sur la TV Philips.

Expose des outils MCP pour :
- Naviguer vers une URL
- Prendre des screenshots
- Exécuter du JavaScript
- Récupérer le HTML / titre de la page
- Simuler des clics / scroll
- Gérer les onglets

Architecture :
    Mac Mini (MCP Server) → ADB forward :9222 → Kiwi Browser (TV Philips)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

logger = logging.getLogger("tv_mcp")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# ── Configuration ────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


TV_IP: str = os.environ.get("TV_IP", "").strip()
TV_ADB_PORT: int = int(os.environ.get("TV_ADB_PORT", "5555"))
TV_ADB_SERIAL: str = os.environ.get("TV_ADB_SERIAL", "").strip()
TV_ALLOW_NETWORK_ADB: bool = _env_bool("TV_ALLOW_NETWORK_ADB")
CDP_LOCAL_PORT: int = int(os.environ.get("CDP_LOCAL_PORT", "9222"))
DASHBOARD_URL: str = os.environ.get("TV_DASHBOARD_URL", "http://127.0.0.1:5174/").strip()
DASHBOARD_TOKEN: str = os.environ.get("TV_DASHBOARD_TOKEN", "").strip()
KIWI_PACKAGE: str = "com.kiwibrowser.browser"
KIWI_ACTIVITY: str = f"{KIWI_PACKAGE}/com.google.android.apps.chrome.Main"

ADB_CMD: str = shutil.which("adb") or "adb"
ADB_TARGET: str = TV_ADB_SERIAL or (f"{TV_IP}:{TV_ADB_PORT}" if TV_IP else "")

ALLOWED_NAVIGATION_SCHEMES = frozenset({"http", "https"})
_dashboard_netloc = urlsplit(DASHBOARD_URL).netloc.lower()
ALLOWED_NAVIGATION_HOSTS = frozenset(
    host.strip().lower()
    for host in os.environ.get("TV_ALLOWED_NAVIGATION_HOSTS", _dashboard_netloc).split(",")
    if host.strip()
)

ALLOWED_KEYCODES = {
    "HOME": "KEYCODE_HOME",
    "KEYCODE_HOME": "KEYCODE_HOME",
    "BACK": "KEYCODE_BACK",
    "KEYCODE_BACK": "KEYCODE_BACK",
    "UP": "KEYCODE_DPAD_UP",
    "DPAD_UP": "KEYCODE_DPAD_UP",
    "KEYCODE_DPAD_UP": "KEYCODE_DPAD_UP",
    "DOWN": "KEYCODE_DPAD_DOWN",
    "DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "KEYCODE_DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "LEFT": "KEYCODE_DPAD_LEFT",
    "DPAD_LEFT": "KEYCODE_DPAD_LEFT",
    "KEYCODE_DPAD_LEFT": "KEYCODE_DPAD_LEFT",
    "RIGHT": "KEYCODE_DPAD_RIGHT",
    "DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "KEYCODE_DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "CENTER": "KEYCODE_DPAD_CENTER",
    "DPAD_CENTER": "KEYCODE_DPAD_CENTER",
    "KEYCODE_DPAD_CENTER": "KEYCODE_DPAD_CENTER",
    "ENTER": "KEYCODE_ENTER",
    "KEYCODE_ENTER": "KEYCODE_ENTER",
}

# ── Helpers ──────────────────────────────────────────────────────────────────


async def run_cmd(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Execute une commande shell et retourne (code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def adb(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Execute une commande ADB."""
    if not ADB_TARGET:
        return -1, "", "TV_ADB_SERIAL ou TV_IP non configuré"
    return await run_cmd(ADB_CMD, "-s", ADB_TARGET, *args, timeout=timeout)


def _is_network_adb_target(target: str) -> bool:
    """Reconnaît les serials ADB TCP sans confondre les émulateurs locaux."""
    return ":" in target and not target.startswith("emulator-")


def adb_configuration_error(target: str, allow_network: bool) -> str | None:
    """Valide la cible avant toute commande ADB ou création de forward CDP."""
    if not target:
        return "TV_ADB_SERIAL ou TV_IP doit être configuré explicitement"
    if _is_network_adb_target(target) and not allow_network:
        return "ADB réseau refusé: définir TV_ALLOW_NETWORK_ADB=true explicitement"
    return None


def validate_navigation_url(url: str) -> str | None:
    """Retourne une erreur si l'URL sort des schémas et hosts autorisés."""
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        return "URL invalide"
    try:
        parsed = urlsplit(url)
        # Accéder à port force aussi la validation des ports mal formés.
        _ = parsed.port
    except ValueError:
        return "URL invalide"
    if parsed.scheme.lower() not in ALLOWED_NAVIGATION_SCHEMES:
        return "Schéma URL non autorisé"
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return "Host URL invalide"
    if parsed.netloc.lower() not in ALLOWED_NAVIGATION_HOSTS:
        return "Host URL non autorisé"
    return None


def resolve_keycode(key: Any) -> str | None:
    """Résout uniquement les touches explicitement autorisées."""
    if not isinstance(key, str):
        return None
    return ALLOWED_KEYCODES.get(key.strip().upper())


def dashboard_launch_url() -> str:
    """Ajoute le jeton de bootstrap sans élargir l'allowlist de navigation."""
    if not DASHBOARD_TOKEN:
        return DASHBOARD_URL
    parsed = urlsplit(DASHBOARD_URL)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "token"]
    query.append(("token", DASHBOARD_TOKEN))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


async def cdp_get(path: str) -> dict[str, Any]:
    """GET sur l'API HTTP CDP."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://localhost:{CDP_LOCAL_PORT}{path}", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            return await resp.json()


async def cdp_put(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """PUT sur l'API HTTP CDP (utilisé pour new tab, activate)."""
    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"http://localhost:{CDP_LOCAL_PORT}{path}",
            params=params or {},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            return await resp.json()


# ── TV Browser Manager ────────────────────────────────────────────────────────


@dataclass
class TVBrowser:
    """Gestionnaire du navigateur Kiwi sur la TV."""

    forward_active: bool = False
    _current_page_id: str | None = None

    async def is_adb_connected(self) -> bool:
        """Inspecte la cible configurée sans lancer de connexion réseau."""
        if adb_configuration_error(ADB_TARGET, TV_ALLOW_NETWORK_ADB):
            return False
        code, stdout, _ = await run_cmd(ADB_CMD, "devices")
        if code != 0:
            return False
        return any(line.startswith(f"{ADB_TARGET}\tdevice") for line in stdout.splitlines())

    async def ensure_adb_connected(self) -> bool:
        """Vérifie que la TV est connectée via ADB."""
        config_error = adb_configuration_error(ADB_TARGET, TV_ALLOW_NETWORK_ADB)
        if config_error:
            logger.error(config_error)
            return False
        if await self.is_adb_connected():
            return True

        if not _is_network_adb_target(ADB_TARGET):
            logger.error("Cible ADB USB absente: %s", ADB_TARGET)
            return False

        logger.info("Connexion ADB réseau à la cible explicitement autorisée %s", ADB_TARGET)
        code, stdout, stderr = await run_cmd(
            ADB_CMD,
            "connect",
            ADB_TARGET,
            timeout=10.0,
        )
        if code != 0 or ("connected" not in stdout and "already" not in stdout):
            logger.error("Échec connexion ADB: %s %s", stdout, stderr)
            return False
        if not await self.is_adb_connected():
            logger.error("La cible ADB n'apparaît pas connectée après adb connect")
            return False
        logger.info("ADB connecté")
        return True

    async def start_cdp_forward(self) -> bool:
        """Établit le forward de port CDP."""
        if self.forward_active:
            return True

        code, stdout, stderr = await adb(
            "forward", f"tcp:{CDP_LOCAL_PORT}", "localabstract:chrome_devtools_remote"
        )
        if code != 0:
            logger.error(f"Échec forward CDP: {stderr}")
            return False

        self.forward_active = True
        logger.info(f"CDP forward :{CDP_LOCAL_PORT} -> TV")
        return True

    async def launch_browser(self) -> bool:
        """Lance Kiwi Browser sur la TV."""
        # Réveiller la TV
        await adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        await asyncio.sleep(1)

        # Vérifier si Kiwi est déjà lancé
        code, stdout, _ = await adb("shell", "pidof", KIWI_PACKAGE)
        if code == 0 and stdout.strip():
            logger.info("Kiwi déjà lancé, focus...")
            await adb(
                "shell",
                "am",
                "start",
                "-n",
                KIWI_ACTIVITY,
                "-d",
                dashboard_launch_url(),
                "-f",
                "0x10000000",
            )
        else:
            logger.info("Lancement Kiwi Browser...")
            await adb(
                "shell",
                "am",
                "start",
                "-n",
                KIWI_ACTIVITY,
                "-d",
                dashboard_launch_url(),
            )

        await asyncio.sleep(4)
        return True

    async def get_page_id(self) -> str | None:
        """Récupère l'ID de la page dashboard."""
        try:
            tabs = await cdp_get("/json/list")
            for tab in tabs:
                if "5174" in tab.get("url", "") or "WAR ROOM" in tab.get("title", ""):
                    self._current_page_id = tab["id"]
                    return tab["id"]
            # Fallback: premier onglet
            if tabs:
                self._current_page_id = tabs[0]["id"]
                return tabs[0]["id"]
        except Exception as e:
            logger.error(f"get_page_id error: {e}")
        return None

    async def ensure_ready(self) -> bool:
        """S'assure que tout est prêt : ADB, forward, navigateur, page."""
        if not await self.ensure_adb_connected():
            return False
        if not await self.start_cdp_forward():
            return False

        # Vérifier si CDP répond
        try:
            await cdp_get("/json/version")
        except Exception:
            await self.launch_browser()
            await asyncio.sleep(3)

        page_id = await self.get_page_id()
        return page_id is not None

    # ── Actions CDP ───────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigue vers une URL."""
        # Utilise /json/new pour créer un nouvel onglet avec l'URL
        try:
            result = await cdp_put("/json/new", {"url": url})
            self._current_page_id = result.get("id")
            return {"ok": True, "url": url, "page_id": result.get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def screenshot(self) -> dict[str, Any]:
        """Prend un screenshot de la TV via ADB (plus fiable que CDP screenshot)."""
        code, stdout, stderr = await adb("exec-out", "screencap", "-p", timeout=10.0)
        if code != 0:
            return {"ok": False, "error": stderr}
        import base64
        return {"ok": True, "image_base64": base64.b64encode(stdout.encode() if isinstance(stdout, str) else stdout).decode()}

    async def get_title(self) -> dict[str, Any]:
        """Récupère le titre de la page active."""
        try:
            tabs = await cdp_get("/json/list")
            for tab in tabs:
                if tab["id"] == self._current_page_id:
                    return {"ok": True, "title": tab["title"], "url": tab["url"]}
            return {"ok": False, "error": "Page not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def list_tabs(self) -> dict[str, Any]:
        """Liste tous les onglets ouverts."""
        try:
            tabs = await cdp_get("/json/list")
            return {"ok": True, "tabs": [{"id": t["id"], "title": t["title"], "url": t["url"]} for t in tabs]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def evaluate(self, expression: str) -> dict[str, Any]:
        """Exécute du JavaScript dans la page et retourne le résultat."""
        # Utilise l'endpoint /json/activate puis CDP via HTTP PUT
        # Pour le JS, on utilise le endpoint HTTP pas le WS
        try:
            # Approche simplifiée: on crée un bookmarklet via navigation
            # Pour des évaluations plus complexes, utiliser Puppeteer
            return {"ok": True, "note": "JS evaluate via CDP HTTP limité. Utiliser Puppeteer pour evaluate complet."}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── MCP Server ───────────────────────────────────────────────────────────────

# Singleton
tv_browser = TVBrowser()


def _tool_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [
                {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
            ]
        },
    }


async def handle_mcp_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Traite un message MCP entrant."""
    method = msg.get("method", "")
    msg_id = msg.get("id")

    # ── Initialize ──
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "tv-browser-mcp",
                    "version": "1.0.0",
                },
            },
        }

    # ── List tools ──
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "tv_navigate",
                        "description": "Navigue vers une URL sur le navigateur Kiwi de la TV Philips",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "URL à ouvrir"}
                            },
                            "required": ["url"],
                        },
                    },
                    {
                        "name": "tv_screenshot",
                        "description": "Prend un screenshot de l'écran TV actuel",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "tv_get_info",
                        "description": "Récupère le titre et l'URL de la page active sur la TV",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "tv_open_dashboard",
                        "description": "Ouvre le dashboard JARVIS War Room sur la TV",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "tv_refresh",
                        "description": "Rafraîchit la page active sur la TV (F5)",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "tv_press_key",
                        "description": "Envoie une touche clavier à la TV (DPAD_UP, DPAD_DOWN, HOME, BACK, etc.)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "description": "Code touche Android (KEYCODE_HOME, DPAD_CENTER, etc.)"}
                            },
                            "required": ["key"],
                        },
                    },
                    {
                        "name": "tv_status",
                        "description": "Vérifie l'état de la connexion TV (ADB, CDP, navigateur)",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }

    # ── Call tool ──
    if method == "tools/call":
        tool_name = msg.get("params", {}).get("name", "")
        arguments = msg.get("params", {}).get("arguments", {})
        if not isinstance(arguments, dict):
            return _tool_result(msg_id, {"ok": False, "error": "Arguments invalides"})

        known_tools = {
            "tv_navigate",
            "tv_screenshot",
            "tv_get_info",
            "tv_open_dashboard",
            "tv_refresh",
            "tv_press_key",
            "tv_status",
        }
        if tool_name not in known_tools:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        # Les arguments contrôlables sont rejetés avant tout adb connect,
        # forward CDP ou lancement du navigateur.
        navigation_url: str | None = None
        keycode: str | None = None
        if tool_name == "tv_navigate":
            navigation_url = arguments.get("url", "")
            validation_error = validate_navigation_url(navigation_url)
            if validation_error:
                result = {"ok": False, "error": validation_error}
                return _tool_result(msg_id, result)
        elif tool_name == "tv_open_dashboard":
            navigation_url = dashboard_launch_url()
            validation_error = validate_navigation_url(navigation_url)
            if validation_error:
                result = {"ok": False, "error": f"Dashboard invalide: {validation_error}"}
                return _tool_result(msg_id, result)
        elif tool_name == "tv_press_key":
            keycode = resolve_keycode(arguments.get("key"))
            if keycode is None:
                return _tool_result(msg_id, {"ok": False, "error": "Touche non autorisée"})

        if tool_name == "tv_status":
            adb_ok = await tv_browser.is_adb_connected()
            cdp_ok = False
            try:
                await cdp_get("/json/version")
                cdp_ok = True
            except Exception:
                pass
            result = {
                "ok": True,
                "adb_connected": adb_ok,
                "cdp_available": cdp_ok,
                "dashboard_url": DASHBOARD_URL,
            }
        else:
            if not await tv_browser.ensure_ready():
                return _tool_result(msg_id, {"ok": False, "error": "TV indisponible ou configuration refusée"})

            if tool_name == "tv_navigate":
                result = await tv_browser.navigate(navigation_url or "")
            elif tool_name == "tv_screenshot":
                result = await tv_browser.screenshot()
            elif tool_name == "tv_get_info":
                result = await tv_browser.get_title()
            elif tool_name == "tv_open_dashboard":
                navigation_result = await tv_browser.navigate(navigation_url or "")
                result = {**navigation_result, "url": DASHBOARD_URL}
            elif tool_name == "tv_refresh":
                code, _, _ = await adb("shell", "input", "keyevent", "KEYCODE_F5")
                result = {"ok": code == 0}
            else:  # tv_press_key, validé ci-dessus
                code, _, _ = await adb("shell", "input", "keyevent", keycode or "")
                result = {"ok": code == 0, "key": keycode}

        return _tool_result(msg_id, result)

    # ── Notifications ──
    if "id" not in msg:
        # notifications/initialized - no response needed
        return {}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def main() -> None:
    """Boucle principale MCP Server (stdio)."""
    logger.info("TV Browser MCP Server démarré")
    logger.info("Cible ADB: %s", ADB_TARGET or "non configurée")
    logger.info(f"CDP: localhost:{CDP_LOCAL_PORT}")
    logger.info(f"Dashboard: {DASHBOARD_URL}")

    # Boucle MCP sur stdin/stdout
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    loop = asyncio.get_event_loop()

    def write_response(data: bytes) -> None:
        """Écrit une réponse sur stdout de manière thread-safe."""
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    while True:
        try:
            # Lire jusqu'au délimiteur newline (MCP utilise \n comme séparateur de messages JSON-RPC)
            line = await reader.readline()
            if not line:
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except json.JSONDecodeError:
                logger.warning(f"JSON invalide: {line_str[:100]}")
                continue

            response = await handle_mcp_message(msg)
            if response:
                resp_bytes = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
                await loop.run_in_executor(None, write_response, resp_bytes)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erreur boucle MCP: {e}")
            continue

    logger.info("MCP Server arrêté")


if __name__ == "__main__":
    asyncio.run(main())
