"""Tâches de démarrage isolées du cycle de vie FastAPI principal."""

from __future__ import annotations

import asyncio
import logging

import config

logger = logging.getLogger("jarvis")


async def connect_tv_adb() -> None:
    """Prépare la connexion ADB TV sans rendre le démarrage bloquant."""

    try:
        tv_ip = getattr(config, "TV_IP", "")
        tv_port = int(getattr(config, "TV_ADB_PORT", "5555") or "5555")
        if not tv_ip:
            return
        proc = await asyncio.create_subprocess_exec(
            "adb",
            "connect",
            f"{tv_ip}:{tv_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        output = (stdout + stderr).decode(errors="replace").strip()
        if "connected" in output.lower() or "already" in output.lower():
            logger.info(
                "[startup] ADB connecté à la TV (%s:%s) — %s",
                tv_ip,
                tv_port,
                output.split("\n")[0][:80],
            )
        else:
            logger.debug(
                "[startup] ADB TV non joignable (%s) : %s", tv_ip, output[:100]
            )
    except Exception as exc:
        logger.debug("[startup] ADB TV skip : %s", exc)
