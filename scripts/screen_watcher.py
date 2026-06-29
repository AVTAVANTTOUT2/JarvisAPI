"""Screen Watcher — capture et analyse l'écran en continu.

Pipeline :
  1. Capture (`screencapture` macOS) toutes les `SCREEN_WATCHER_INTERVAL` secondes.
  2. Hash + diff pixel (NumPy non requis — Pillow suffit).
  3. Si changement >= `SCREEN_CHANGE_THRESHOLD` % → on note l'app au premier plan
     et on incrémente `app_usage`. En dessous → idle counter.
  4. Si changement >= `SCREEN_ANALYSIS_THRESHOLD` % → analyse Ollama vision locale.
     Le résultat (app, activity, mood, notable) est stocké dans `screen_activity`.
  5. Si `analysis["notable"]` est non vide → callback `on_notable` (le daemon
     décide si une notif vocale doit être déclenchée).

Le screen watcher ne parle PAS à Claude API directement — c'est le daemon qui
fait l'arbitrage et déclenche éventuellement Claude pour formuler une notif.
"""

import asyncio
import base64
import hashlib
import json
import logging
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

import config
from database import save_screen_activity, upsert_app_usage

logger = logging.getLogger(__name__)


class ScreenWatcher:
    def __init__(self) -> None:
        self.enabled = bool(getattr(config, "SCREEN_WATCHER_ENABLED", True))
        self.interval = int(getattr(config, "SCREEN_WATCHER_INTERVAL", 12))
        self.change_threshold = float(getattr(config, "SCREEN_CHANGE_THRESHOLD", 5))
        self.analysis_threshold = float(getattr(config, "SCREEN_ANALYSIS_THRESHOLD", 15))
        self.ollama_model = str(getattr(config, "SCREEN_VISION_MODEL", "qwen2.5-vl:7b"))
        self.ollama_url = str(getattr(config, "OLLAMA_URL", "http://localhost:11434"))
        self.device = str(getattr(config, "DEVICE_ID", "mac_mini"))

        # Anti-spam : desactiver Ollama apres N echecs consecutifs, retenter apres cooldown
        self._ollama_available: bool = True
        self._ollama_failures: int = 0
        self._ollama_max_failures: int = int(getattr(config, "SCREEN_OLLAMA_MAX_FAILURES", "5"))
        self._ollama_cooldown_s: float = float(getattr(config, "SCREEN_OLLAMA_COOLDOWN_S", "300"))
        self._ollama_next_retry: float = 0.0

        self.last_image: Image.Image | None = None
        self.last_hash: str | None = None
        self.last_app: str | None = None
        self.last_app_time = time.time()
        self.idle_seconds = 0
        self.idle_alerted = False
        self.running = False

        # Callbacks asynchrones définis par le daemon
        self.on_notable = None  # async (notable_text: str, context: dict) -> None
        self.on_idle = None     # async (idle_minutes: int) -> None

    async def start(self) -> None:
        """Boucle principale du screen watcher."""
        if not self.enabled:
            logger.info("[screen] désactivé (SCREEN_WATCHER_ENABLED=false)")
            return

        self.running = True
        logger.info(
            "[screen] démarré — interval=%ss, seuils=%s%%/%s%%",
            self.interval, self.change_threshold, self.analysis_threshold,
        )

        while self.running:
            try:
                await self._tick()
            except Exception as e:
                logger.exception("[screen] erreur tick : %s", e)
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self.running = False

    async def _tick(self) -> None:
        """Un cycle de capture + analyse."""
        image = await self._capture()
        if image is None:
            return

        current_hash = self._hash_image(image)
        if current_hash == self.last_hash:
            self.idle_seconds += self.interval
            await self._check_idle()
            return

        change_pct = self._compute_diff(image, self.last_image) if self.last_image else 100.0
        self.last_image = image
        self.last_hash = current_hash

        if change_pct < self.change_threshold:
            self.idle_seconds += self.interval
            await self._check_idle()
            return

        self.idle_seconds = 0
        self.idle_alerted = False

        current_app = await self._get_frontmost_app()

        # Tracker le temps par app : on prolonge la fenêtre tant que l'app
        # courante reste la même. Quand elle change, on persiste le temps cumulé.
        if current_app and current_app != self.last_app:
            if self.last_app:
                elapsed = int(time.time() - self.last_app_time)
                if elapsed > 0:
                    upsert_app_usage(self.device, self.last_app, elapsed)
            self.last_app = current_app
            self.last_app_time = time.time()

        if change_pct >= self.analysis_threshold:
            analysis = await self._analyze_with_ollama(image)
            if analysis:
                save_screen_activity(
                    device=self.device,
                    app=analysis.get("app") or current_app,
                    activity=analysis.get("activity", ""),
                    mood=analysis.get("mood", "unknown"),
                    notable=analysis.get("notable"),
                    screenshot_hash=current_hash,
                    change_pct=round(change_pct, 1),
                )
                notable = analysis.get("notable")
                if notable and self.on_notable:
                    try:
                        await self.on_notable(notable, analysis)
                    except Exception as e:
                        logger.warning("[screen] on_notable callback : %s", e)
            else:
                save_screen_activity(
                    device=self.device,
                    app=current_app,
                    activity="",
                    screenshot_hash=current_hash,
                    change_pct=round(change_pct, 1),
                )
        else:
            save_screen_activity(
                device=self.device,
                app=current_app,
                activity="minor_change",
                screenshot_hash=current_hash,
                change_pct=round(change_pct, 1),
            )

    async def _capture(self) -> Image.Image | None:
        """Capture l'écran via `screencapture` macOS."""
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "/usr/sbin/screencapture", "-x", "-C", "-t", "png", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)

            img = Image.open(tmp_path)
            img.load()
            img = img.resize((1280, 800), Image.Resampling.LANCZOS)
            return img
        except Exception as e:
            logger.warning("[screen] capture échouée : %s", e)
            return None
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _compute_diff(self, img1: Image.Image, img2: Image.Image) -> float:
        """% de pixels qui ont changé de plus de 30 niveaux entre deux images."""
        try:
            if img1.size != img2.size:
                img2 = img2.resize(img1.size)
            g1 = img1.convert("L").getdata()
            g2 = img2.convert("L").getdata()
            total = len(g1)
            if total == 0:
                return 100.0
            changed = sum(1 for a, b in zip(g1, g2) if abs(a - b) > 30)
            return (changed / total) * 100
        except Exception:
            return 100.0

    def _hash_image(self, img: Image.Image) -> str:
        """Hash MD5 d'une vignette 64×64 en niveaux de gris."""
        small = img.resize((64, 64)).convert("L")
        return hashlib.md5(small.tobytes()).hexdigest()

    async def _get_frontmost_app(self) -> str | None:
        """Nom de l'app au premier plan (AppleScript)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to return name of first application process whose frontmost is true',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            name = (stdout or b"").decode().strip()
            return name or None
        except Exception:
            return None

    async def _analyze_with_ollama(self, image: Image.Image) -> dict | None:
        """Analyse l'image avec le LLM vision local (Ollama).

        Le prompt force une sortie JSON ultra-courte. Le résultat est texte pur
        — il ne quitte JAMAIS la machine. Coût Claude API = 0.

        Anti-spam : après N échecs consécutifs, désactive Ollama temporairement
        (cooldown de 5 min) pour éviter le flood de logs. Réessaie automatiquement.
        """
        # Cooldown : vérifier si on peut réessayer
        if not self._ollama_available:
            if time.time() < self._ollama_next_retry:
                return None  # silencieux, pas de log
            # Fin du cooldown → retenter
            logger.info("[screen] Re-tentative Ollama apres cooldown")
            self._ollama_available = True
            self._ollama_failures = 0

        try:
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            img_b64 = base64.b64encode(buffer.getvalue()).decode()

            prompt = (
                "Décris en 1 ligne ce que tu vois sur cet écran.\n"
                "Retourne UNIQUEMENT ce JSON, rien d'autre :\n"
                '{"app": "nom de l\'application visible", '
                '"activity": "ce que l\'utilisateur fait en 5 mots max", '
                '"mood": "focused|idle|distracted|stuck|browsing", '
                '"notable": "info pertinente (erreur, site, notification) ou null"}\n\n'
                "Exemples :\n"
                '{"app": "VS Code", "activity": "code Python", "mood": "focused", "notable": null}\n'
                '{"app": "Terminal", "activity": "erreur rouge", "mood": "stuck", '
                '"notable": "erreur Python ModuleNotFoundError dans le terminal"}\n'
                '{"app": "Safari", "activity": "YouTube", "mood": "distracted", '
                '"notable": "regarde YouTube depuis un moment"}\n'
                '{"app": "Finder", "activity": "navigation fichiers", "mood": "browsing", "notable": null}'
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.ollama_model,
                        "prompt": prompt,
                        "images": [img_b64],
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 100},
                    },
                )
                response.raise_for_status()
                result = response.json()

            # Succès → reset le compteur d'échecs
            self._ollama_failures = 0

            text = (result.get("response") or "").strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    return None
            return None
        except Exception as e:
            self._ollama_failures += 1
            if self._ollama_failures >= self._ollama_max_failures:
                self._ollama_available = False
                self._ollama_next_retry = time.time() + self._ollama_cooldown_s
                logger.warning(
                    "[screen] Ollama indisponible (%d echecs) — desactive pour %ds",
                    self._ollama_failures, int(self._ollama_cooldown_s),
                )
            else:
                logger.debug("[screen] analyse Ollama echouee (%d/%d) : %s",
                           self._ollama_failures, self._ollama_max_failures, e)
            return None

    async def _check_idle(self) -> None:
        """Notifie le daemon si l'utilisateur est inactif depuis longtemps."""
        idle_minutes = self.idle_seconds // 60
        if idle_minutes >= 20 and not self.idle_alerted:
            self.idle_alerted = True
            if self.on_idle:
                try:
                    await self.on_idle(idle_minutes)
                except Exception as e:
                    logger.warning("[screen] on_idle callback : %s", e)


screen_watcher = ScreenWatcher()
