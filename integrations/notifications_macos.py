"""Notifications bureau macOS natives via AppleScript `display notification`."""

from __future__ import annotations

import asyncio
import logging

import config

from ._applescript import escape_applescript_string, is_macos_with_osascript, run_applescript

logger = logging.getLogger(__name__)

DEFAULT_SOUND = "Glass"


class MacNotifier:
    """Bandeau système (Centre de notifications macOS)."""

    def is_available(self) -> bool:
        return is_macos_with_osascript()

    def _run(self, title: str, message: str, subtitle: str, sound: str) -> bool:
        if not config.DESKTOP_NOTIFICATIONS:
            return False
        if not self.is_available():
            return False
        t = escape_applescript_string(title).replace("\\n", " ")
        m = escape_applescript_string(message).replace("\\n", " ")
        s = escape_applescript_string(subtitle).replace("\\n", " ")
        snd = escape_applescript_string(sound or config.NOTIFICATION_SOUND or DEFAULT_SOUND).replace("\\n", " ")
        script = f'display notification "{m}" with title "{t}" subtitle "{s}" sound name "{snd}"'
        result = run_applescript(script, timeout=15)
        if not result.ok:
            logger.error("[mac_notifier] %s", result.stderr)
            return False
        return True

    async def notify(
        self,
        title: str,
        message: str,
        subtitle: str = "",
        sound: str | None = None,
    ) -> bool:
        snd = sound if sound is not None else (config.NOTIFICATION_SOUND or DEFAULT_SOUND)
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(
            None,
            lambda: self._run(title, message, subtitle, snd),
        )
        if ok:
            logger.info("[mac_notifier] notify : %s", title[:60])
        return bool(ok)

    async def notify_urgent(self, title: str, message: str) -> bool:
        return await self.notify(title, message, subtitle="", sound="Sosumi")


mac_notifier = MacNotifier()
