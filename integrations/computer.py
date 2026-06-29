"""Contrôle local macOS — shell sécurisé, AppleScript, infos système."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from pathlib import Path

import config

from ._applescript import run_applescript_async

logger = logging.getLogger(__name__)

BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+~\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+mkfs\b", re.IGNORECASE),
    re.compile(r":\(\)\s*:\s*\|:", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*bash", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*bash", re.IGNORECASE),
]


class ComputerControl:
    """Interface subprocess sécurisée avec le Mac de l'utilisateur."""

    def __init__(self) -> None:
        self.allowed = config.COMPUTER_ACCESS.lower() == "true"
        self.shell = config.COMPUTER_SHELL
        self.home = str(Path.home())
        self.timeout = config.COMPUTER_TIMEOUT
        self.blocked_patterns = BLOCKED_PATTERNS

    def is_safe(self, command: str) -> tuple[bool, str]:
        if not command or not command.strip():
            return False, "commande vide"
        for pat in self.blocked_patterns:
            if pat.search(command):
                return False, f"pattern bloqué : {pat.pattern}"
        return True, ""

    async def run(self, command: str, timeout: int | None = None, cwd: str | None = None) -> dict:
        if not self.allowed:
            return {"ok": False, "error": "Accès ordinateur désactivé"}
        safe, reason = self.is_safe(command)
        if not safe:
            return {"ok": False, "error": f"Commande bloquée : {reason}"}

        to = timeout if timeout is not None else self.timeout
        logger.info("[computer] Exécution : %s", command[:500])

        env = {**os.environ, "HOME": self.home, "USER": os.getenv("USER", "")}
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or self.home,
                executable=self.shell,
                env=env,
            )
        except Exception as e:
            logger.warning("[computer] spawn : %s", e)
            return {"ok": False, "error": str(e), "command": command}

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=to)
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return {
                "ok": False,
                "error": f"Timeout après {to}s",
                "command": command,
            }

        out = stdout.decode("utf-8", errors="replace")[:5000]
        err = stderr.decode("utf-8", errors="replace")[:2000]
        return {
            "ok": process.returncode == 0,
            "command": command,
            "stdout": out,
            "stderr": err,
            "returncode": process.returncode,
        }

    async def open_app(self, app_name: str) -> dict:
        if not app_name.strip():
            return {"ok": False, "error": "nom d'application vide"}
        cmd = f"open -a {shlex.quote(app_name.strip())}"
        return await self.run(cmd, timeout=30)

    async def run_applescript(self, script: str) -> dict:
        """Exécute AppleScript via ``osascript -e`` (sans shell fragile)."""
        if not script.strip():
            return {"ok": False, "error": "script vide"}
        as_timeout = 30.0
        for attempt in range(2):
            result = await run_applescript_async(
                script.strip(),
                timeout=as_timeout,
                extra_env={"HOME": self.home},
                cwd=self.home,
            )
            if result.reason == "timeout":
                logger.warning("[computer] AppleScript timeout (tentative %s)", attempt + 1)
                if attempt == 0:
                    continue
                return {"ok": False, "error": "AppleScript timeout"}
            return {
                "ok": result.ok,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
            }
        return {"ok": False, "error": "AppleScript échec"}

    async def get_clipboard(self) -> str:
        r = await self.run("pbpaste", timeout=10)
        if r.get("ok"):
            return r.get("stdout", "")
        return ""

    async def set_clipboard(self, text: str) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(input=text.encode("utf-8", errors="replace")),
                timeout=15,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "pbcopy timeout"}
        ok = proc.returncode == 0
        return {"ok": ok, "message": "Presse-papiers mis à jour." if ok else err.decode("utf-8", errors="replace")[:500]}

    async def get_running_apps(self) -> list[str]:
        script = (
            'tell application "System Events" to get name of every application process '
            "whose visible is true"
        )
        r = await self.run_applescript(script)
        if not r.get("ok"):
            return []
        raw = (r.get("stdout") or "").strip()
        if not raw:
            return []
        parts = re.split(r",\s*", raw)
        return [p.strip() for p in parts if p.strip()][:80]

    async def get_battery(self) -> dict:
        r = await self.run("pmset -g batt", timeout=10)
        out = r.get("stdout", "") if r.get("ok") else ""
        pct = None
        m = re.search(r"(\d+)\s*%", out)
        if m:
            pct = int(m.group(1))
        status = "unknown"
        if "discharging" in out.lower():
            status = "discharging"
        elif "charging" in out.lower() or "charged" in out.lower():
            status = "charging"
        return {"battery_percent": pct, "battery_raw": out[:800], "status": status}

    async def get_wifi(self) -> dict:
        airport = (
            "/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport"
        )
        r = await self.run(f"{shlex.quote(airport)} -I", timeout=15)
        out = r.get("stdout", "") if r.get("stdout") else r.get("stderr", "")
        ssid = None
        m = re.search(r"^\s*SSID:\s*(.+)$", out, re.MULTILINE)
        if m:
            ssid = m.group(1).strip()
        rssi = None
        m2 = re.search(r"^\s*agrCtlRSSI:\s*(-?\d+)", out, re.MULTILINE)
        if m2:
            rssi = int(m2.group(1))
        return {"wifi_ssid": ssid, "wifi_rssi": rssi, "wifi_raw": out[:1200]}

    async def get_disk_space(self) -> dict:
        r = await self.run("df -h /", timeout=10)
        lines = (r.get("stdout") or "").strip().splitlines()
        info = {"disk_df": r.get("stdout", "")[:2000], "ok_df": r.get("ok", False)}
        if len(lines) >= 2:
            cols = lines[1].split()
            if len(cols) >= 5:
                info["disk_size"] = cols[1]
                info["disk_used"] = cols[2]
                info["disk_avail"] = cols[3]
                info["disk_use_pct"] = cols[4]
        return info

    async def find_files(self, query: str, path: str | None = None) -> list[str]:
        q = re.sub(r"[^\w\s.\-]", "", (query or "").strip())[:200]
        if not q:
            return []
        base = path or self.home
        try:
            base_exp = str(Path(base).expanduser().resolve())
        except Exception:
            base_exp = self.home
        if not base_exp.startswith(self.home) and base_exp != "/":
            base_exp = self.home
        pattern = f"*{q}*"
        cmd = (
            f"find {shlex.quote(base_exp)} -iname {shlex.quote(pattern)} "
            f"-maxdepth 6 2>/dev/null | head -20"
        )
        r = await self.run(cmd, timeout=60)
        if not r.get("stdout"):
            return []
        return [ln.strip() for ln in r["stdout"].splitlines() if ln.strip()][:20]

    async def get_active_window(self) -> str:
        script = (
            'tell application "System Events" to get name of first application process '
            "whose frontmost is true"
        )
        r = await self.run_applescript(script)
        return (r.get("stdout") or "").strip() if r.get("ok") else ""


computer = ComputerControl()
