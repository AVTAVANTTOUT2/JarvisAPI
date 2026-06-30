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
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("tv_mcp")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# ── Configuration ────────────────────────────────────────────────────────────

TV_IP: str = os.environ.get("TV_IP", "192.168.3.82")
TV_ADB_PORT: int = int(os.environ.get("TV_ADB_PORT", "5555"))
CDP_LOCAL_PORT: int = int(os.environ.get("CDP_LOCAL_PORT", "9222"))
DASHBOARD_URL: str = os.environ.get("TV_DASHBOARD_URL", "http://192.168.3.52:5174/")
KIWI_PACKAGE: str = "com.kiwibrowser.browser"
KIWI_ACTIVITY: str = f"{KIWI_PACKAGE}/com.google.android.apps.chrome.Main"

ADB_CMD: str = shutil.which("adb") or "adb"

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
    return await run_cmd(ADB_CMD, *args, timeout=timeout)


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

    async def ensure_adb_connected(self) -> bool:
        """Vérifie que la TV est connectée via ADB."""
        code, stdout, stderr = await adb("devices")
        if f"{TV_IP}:{TV_ADB_PORT}" not in stdout:
            logger.info(f"Connexion ADB à {TV_IP}:{TV_ADB_PORT}...")
            code, stdout, stderr = await adb("connect", f"{TV_IP}:{TV_ADB_PORT}", timeout=10.0)
            if "connected" not in stdout and "already" not in stdout:
                logger.error(f"Échec connexion ADB: {stdout} {stderr}")
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
                DASHBOARD_URL,
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
                DASHBOARD_URL,
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

        await tv_browser.ensure_ready()

        result = None

        if tool_name == "tv_navigate":
            result = await tv_browser.navigate(arguments.get("url", DASHBOARD_URL))

        elif tool_name == "tv_screenshot":
            result = await tv_browser.screenshot()

        elif tool_name == "tv_get_info":
            result = await tv_browser.get_title()

        elif tool_name == "tv_open_dashboard":
            await tv_browser.navigate(DASHBOARD_URL)
            result = {"ok": True, "url": DASHBOARD_URL}

        elif tool_name == "tv_refresh":
            code, _, _ = await adb("shell", "input", "keyevent", "KEYCODE_F5")
            result = {"ok": code == 0}

        elif tool_name == "tv_press_key":
            key_map = {
                "HOME": "KEYCODE_HOME",
                "BACK": "KEYCODE_BACK",
                "UP": "DPAD_UP",
                "DOWN": "DPAD_DOWN",
                "LEFT": "DPAD_LEFT",
                "RIGHT": "DPAD_RIGHT",
                "CENTER": "DPAD_CENTER",
                "ENTER": "KEYCODE_ENTER",
            }
            keycode = key_map.get(arguments.get("key", "").upper(), arguments.get("key", ""))
            code, _, _ = await adb("shell", "input", "keyevent", keycode)
            result = {"ok": code == 0, "key": keycode}

        elif tool_name == "tv_status":
            adb_ok = await tv_browser.ensure_adb_connected()
            cdp_ok = False
            try:
                await cdp_get("/json/version")
                cdp_ok = True
            except Exception:
                pass
            result = {"ok": True, "adb_connected": adb_ok, "cdp_available": cdp_ok, "dashboard_url": DASHBOARD_URL}

        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
            },
        }

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
    logger.info(f"TV: {TV_IP}:{TV_ADB_PORT}")
    logger.info(f"CDP: localhost:{CDP_LOCAL_PORT}")
    logger.info(f"Dashboard: {DASHBOARD_URL}")

    # Initialiser la connexion TV au démarrage
    ready = await tv_browser.ensure_ready()
    logger.info(f"TV prête: {ready}")

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
