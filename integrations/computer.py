"""Contrôle local macOS sans shell — AppleScript et infos système."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Sequence

import config

from ._applescript import run_applescript_async

logger = logging.getLogger(__name__)

_SYSTEM_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
_OPEN = "/usr/bin/open"
_PBPASTE = "/usr/bin/pbpaste"
_PBCOPY = "/usr/bin/pbcopy"
_PMSET = "/usr/bin/pmset"
_DF = "/bin/df"
_FIND = "/usr/bin/find"
_AIRPORT = (
    "/System/Library/PrivateFrameworks/Apple80211.framework/"
    "Versions/Current/Resources/airport"
)


def _minimal_child_environment(home: str) -> dict[str, str]:
    """Environnement déterministe qui n'hérite jamais des secrets du serveur."""
    return {
        "PATH": _SYSTEM_PATH,
        "HOME": home,
        "USER": Path(home).name,
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


class ComputerControl:
    """Helpers macOS opt-in limités à des argv fixes et validés."""

    def __init__(self) -> None:
        self.allowed = config.COMPUTER_ACCESS
        self.shell = config.COMPUTER_SHELL
        self.home = str(Path.home())
        self.timeout = config.COMPUTER_TIMEOUT

    def _validate_argv(self, argv: tuple[str, ...]) -> tuple[bool, str]:
        """Valide les seules formes de commandes utilisées par les helpers."""
        if argv in {
            (_PBPASTE,),
            (_PBCOPY,),
            (_PMSET, "-g", "batt"),
            (_AIRPORT, "-I"),
            (_DF, "-h", "/"),
        }:
            return True, ""

        if len(argv) == 3 and argv[:2] == (_OPEN, "-a"):
            name = argv[2]
            if not (
                0 < len(name) <= 128
                and not any(ord(char) < 32 for char in name)
                and "/" not in name
                and "\\" not in name
            ):
                return False, "nom d'application invalide"
            # `open_app` s'exécute sans confirmation : une allowlist renseignée
            # borne ce que peut lancer un bloc ```action``` produit sous
            # injection de prompt. Vide = comportement historique.
            allowed = config.COMPUTER_ALLOWED_APPS
            if allowed and name.strip().lower() not in allowed:
                return False, "application hors de COMPUTER_ALLOWED_APPS"
            return True, ""

        if len(argv) == 6 and argv[0] == _FIND:
            base, depth_flag, depth, name_flag, pattern = argv[1:]
            try:
                base_path = Path(base).resolve()
                home_path = Path(self.home).resolve()
                within_home = (
                    base_path == home_path or base_path.is_relative_to(home_path)
                )
            except (OSError, RuntimeError):
                within_home = False
            query = (
                pattern[1:-1]
                if pattern.startswith("*") and pattern.endswith("*")
                else ""
            )
            if (
                within_home
                and depth_flag == "-maxdepth"
                and depth == "6"
                and name_flag == "-iname"
                and 0 < len(query) <= 200
                and re.fullmatch(r"[\w\s.\-]+", query) is not None
            ):
                return True, ""
            return False, "arguments find invalides"

        return False, "commande absente de l'allowlist interne"

    async def _run_argv(
        self,
        argv: Sequence[str],
        *,
        timeout: int | None = None,
        input_data: bytes | None = None,
    ) -> dict:
        if not self.allowed:
            return {"ok": False, "error": "Accès ordinateur désactivé"}
        normalized = tuple(str(arg) for arg in argv)
        valid, reason = self._validate_argv(normalized)
        if not valid:
            logger.warning("[computer] argv refusés : %s", reason)
            return {"ok": False, "error": f"Commande bloquée : {reason}"}

        to = timeout if timeout is not None else self.timeout
        logger.info("[computer] Exécution argv : %r", normalized)

        try:
            process = await asyncio.create_subprocess_exec(
                *normalized,
                stdin=asyncio.subprocess.PIPE if input_data is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.home,
                env=_minimal_child_environment(self.home),
                start_new_session=True,
            )
        except Exception as e:
            logger.warning("[computer] spawn : %s", e)
            return {"ok": False, "error": str(e), "argv": list(normalized)}

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_data),
                timeout=to,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return {
                "ok": False,
                "error": f"Timeout après {to}s",
                "argv": list(normalized),
            }

        out = stdout.decode("utf-8", errors="replace")[:5000]
        err = stderr.decode("utf-8", errors="replace")[:2000]
        return {
            "ok": process.returncode == 0,
            "argv": list(normalized),
            "stdout": out,
            "stderr": err,
            "returncode": process.returncode,
        }

    async def run(
        self,
        command: str,
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> dict:
        """API legacy confinée : aucune chaîne arbitraire n'est plus exécutée."""
        del timeout, cwd
        logger.warning("[computer] run(str) est déprécié et refusé")
        return {
            "ok": False,
            "error": "ComputerControl.run(str) est désactivé; utilisez un helper argv dédié",
            "command": command,
        }

    async def open_app(self, app_name: str) -> dict:
        if not app_name.strip():
            return {"ok": False, "error": "nom d'application vide"}
        return await self._run_argv((_OPEN, "-a", app_name.strip()), timeout=30)

    async def run_applescript(self, script: str) -> dict:
        """Exécute AppleScript via ``osascript -e`` (sans shell fragile)."""
        if not self.allowed:
            return {"ok": False, "error": "Accès ordinateur désactivé"}
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
        r = await self._run_argv((_PBPASTE,), timeout=10)
        if r.get("ok"):
            return r.get("stdout", "")
        return ""

    async def set_clipboard(self, text: str) -> dict:
        result = await self._run_argv(
            (_PBCOPY,),
            timeout=15,
            input_data=text.encode("utf-8", errors="replace"),
        )
        return {
            "ok": result.get("ok", False),
            "message": "Presse-papiers mis à jour."
            if result.get("ok")
            else result.get("stderr") or result.get("error", "Échec pbcopy"),
        }

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
        r = await self._run_argv((_PMSET, "-g", "batt"), timeout=10)
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
        r = await self._run_argv((_AIRPORT, "-I"), timeout=15)
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
        r = await self._run_argv((_DF, "-h", "/"), timeout=10)
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
            base_path = Path(base).expanduser().resolve()
            home_path = Path(self.home).resolve()
            if base_path != home_path and not base_path.is_relative_to(home_path):
                base_path = home_path
        except (OSError, RuntimeError):
            base_path = Path(self.home)
        pattern = f"*{q}*"
        r = await self._run_argv(
            (_FIND, str(base_path), "-maxdepth", "6", "-iname", pattern),
            timeout=60,
        )
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
