"""Screen Watcher — capture et analyse l'écran en continu.

Pipeline :
  1. Capture (`screencapture` macOS) toutes les `SCREEN_WATCHER_INTERVAL` secondes
     en résolution native (sans resize).
  2. Détection de la fenêtre active via osascript (app + bounds).
  3. Crop de la fenêtre active + resize proportionnel si > MAX_ANALYSIS_WIDTH.
  4. Hash + diff pixel sur le crop (thumbnails 64×64 niveaux de gris).
  5. Si changement >= `SCREEN_CHANGE_THRESHOLD` % → on tracke l'app et on
     incrémente `app_usage` chaque cycle.
  6. Si changement >= `SCREEN_ANALYSIS_THRESHOLD` % → analyse Ollama vision locale.
     Le résultat (app, activity, mood, notable) est stocké dans `screen_activity`.
  7. Si `analysis["notable"]` est non vide → callback `on_notable` (le daemon
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

# ── Constantes ────────────────────────────────────────────────────────────────
_MAX_ANALYSIS_WIDTH_FROM_CONFIG = getattr(config, "SCREEN_MAX_ANALYSIS_WIDTH", None)
MAX_ANALYSIS_WIDTH: int = (
    int(_MAX_ANALYSIS_WIDTH_FROM_CONFIG)
    if _MAX_ANALYSIS_WIDTH_FROM_CONFIG is not None
    else 1280
)
CAPTURE_TIMEOUT: int = 5
OSASCRIPT_TIMEOUT: int = 3
_JPEG_QUALITY_FROM_CONFIG = getattr(config, "SCREEN_JPEG_QUALITY", None)
JPEG_QUALITY: int = int(_JPEG_QUALITY_FROM_CONFIG) if _JPEG_QUALITY_FROM_CONFIG is not None else 70


# États explicites exposés à Control / API
SCREEN_WATCHER_STATES = frozenset({
    "stopped",
    "starting",
    "running",
    "stopping",
    "error",
    "blocked_ollama",
    "disabled",
})


class ScreenWatcher:
    def __init__(self) -> None:
        self.enabled = bool(getattr(config, "SCREEN_WATCHER_ENABLED", True))
        self.interval = int(
            getattr(config, "SCREEN_WATCHER_INTERVAL", None)
            or getattr(config, "SCREEN_WATCHER_INTERVAL_SECONDS", 12)
        )
        self.change_threshold = float(getattr(config, "SCREEN_CHANGE_THRESHOLD", 5))
        self.analysis_threshold = float(getattr(config, "SCREEN_ANALYSIS_THRESHOLD", 15))
        self.ollama_model = str(getattr(config, "SCREEN_VISION_MODEL", "qwen2.5vl:7b"))
        self.ollama_url = str(getattr(config, "OLLAMA_URL", "http://localhost:11434"))
        self.device = str(getattr(config, "DEVICE_ID", "mac_mini"))
        self.max_analysis_width = MAX_ANALYSIS_WIDTH
        self.capture_timeout = CAPTURE_TIMEOUT
        self.osascript_timeout = OSASCRIPT_TIMEOUT
        self.jpeg_quality = JPEG_QUALITY

        # Anti-spam : desactiver Ollama apres N echecs consecutifs, retenter apres cooldown
        self._ollama_available: bool = True
        self._ollama_failures: int = 0
        self._ollama_max_failures: int = int(getattr(config, "SCREEN_OLLAMA_MAX_FAILURES", "5"))
        self._ollama_cooldown_s: float = float(getattr(config, "SCREEN_OLLAMA_COOLDOWN_S", "300"))
        self._ollama_next_retry: float = 0.0

        # Anti-RAM-kill : delai minimum entre deux analyses vision (evite
        # l'accumulation memoire Ollama qwen2.5-vl:7b (~5 GB par appel) qui
        # cause un OOM kill macOS sur Mac Mini 16 Go apres ~100 appels).
        self._ollama_min_interval_s: float = float(
            getattr(config, "SCREEN_OLLAMA_MIN_INTERVAL_S", "120")
        )
        self._last_ollama_call: float = 0.0

        # Resolution logique de l'écran principal (points) — pour le scale factor retina
        self._screen_point_width: int = 0
        self._screen_point_height: int = 0

        self.last_image: Image.Image | None = None
        self.last_hash: str | None = None
        self.last_app: str | None = None
        self.last_app_time: float = time.time()
        self.idle_seconds: int = 0
        self.idle_alerted: bool = False
        self.running: bool = False
        self._status: str = "disabled" if not self.enabled else "stopped"
        self._status_detail: str | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self.last_heartbeat: float | None = None
        self.last_capture_at: float | None = None
        self.last_analysis_at: float | None = None
        self.error_count: int = 0

        # Callbacks asynchrones définis par le daemon
        self.on_notable = None  # async (notable_text: str, context: dict) -> None
        self.on_idle = None     # async (idle_minutes: int) -> None
        self._vision_deferred_logged: float = 0.0
        self._vision_task: asyncio.Task[dict | None] | None = None

    @property
    def status(self) -> str:
        if not self.enabled and self._status != "running":
            return "disabled"
        return self._status

    @staticmethod
    def _is_voice_busy() -> bool:
        try:
            from audio.voice_queue import voice_queue

            return voice_queue.voice_busy or voice_queue.user_conversation_active
        except Exception:
            return False

    def status_payload(self) -> dict:
        """État détaillé pour Control / API (jamais dérivé du daemon seul)."""
        from datetime import datetime, timezone

        def _iso(ts: float | None) -> str | None:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        return {
            "service": "screen_watcher",
            "id": "screen_watcher",
            "name": "Screen Watcher",
            "description": "Analyse ecran Ollama vision",
            "category": "monitoring",
            "status": self.status,
            "state": self.status,
            "running": self.status == "running",
            "can_control": True,
            "autostart": bool(self.enabled),
            "last_heartbeat": _iso(self.last_heartbeat),
            "last_capture_at": _iso(self.last_capture_at),
            "last_analysis_at": _iso(self.last_analysis_at),
            "vision_model": self.ollama_model,
            "error_count": self.error_count,
            "detail": self._status_detail,
            "task_running": bool(self._loop_task and not self._loop_task.done()),
        }

    def refresh_config_enabled(self) -> None:
        """Recharge SCREEN_WATCHER_ENABLED sans écraser un arrêt manuel."""
        self.enabled = bool(getattr(config, "SCREEN_WATCHER_ENABLED", True))
        if not self.enabled and self._status not in ("running", "starting", "stopping"):
            self._status = "disabled"

    # ── Contrôle ────────────────────────────────────────────────────────────

    async def ensure_started(self, *, require_ollama: bool = True, autostart: bool = False) -> dict:
        """Démarre la boucle si possible. Ne modifie pas SCREEN_WATCHER_ENABLED."""
        async with self._start_lock:
            self.refresh_config_enabled()
            if not self.enabled:
                self._status = "disabled"
                self._status_detail = "SCREEN_WATCHER_ENABLED=false"
                logger.info("[screen] blocked: disabled by config")
                return {
                    "ok": False,
                    "service": "screen_watcher",
                    "status": "disabled",
                    "message": "Screen Watcher désactivé (SCREEN_WATCHER_ENABLED=false)",
                }

            if self.running and self._loop_task and not self._loop_task.done():
                return {
                    "ok": True,
                    "service": "screen_watcher",
                    "status": "running",
                    "message": "Déjà actif",
                    **self.status_payload(),
                }

            if require_ollama:
                from integrations.ollama_control import check_ollama_health

                health = await asyncio.to_thread(check_ollama_health)
                if not health.get("healthy"):
                    self._status = "blocked_ollama"
                    self._status_detail = "Ollama unavailable"
                    logger.info("[screen] blocked: Ollama unavailable")
                    return {
                        "ok": False,
                        "service": "screen_watcher",
                        "status": "blocked_ollama",
                        "message": "Impossible de démarrer Screen Watcher : Ollama est arrêté.",
                        "ollama": health,
                    }
                if not health.get("vision_model_available"):
                    self._status = "blocked_ollama"
                    self._status_detail = health.get("error") or "vision model missing"
                    logger.info("[screen] blocked: vision model missing")
                    return {
                        "ok": False,
                        "service": "screen_watcher",
                        "status": "blocked_ollama",
                        "message": health.get("error")
                        or "Ollama fonctionne, mais aucun modèle vision configuré n'est disponible.",
                        "ollama": health,
                    }
                resolved = health.get("vision_model_resolved")
                if resolved:
                    self.ollama_model = str(resolved)

            self._status = "starting"
            self._status_detail = "autostart" if autostart else "manual"
            self._loop_task = asyncio.create_task(self.start(), name="screen_watcher_loop")
            # Preuve de readiness : heartbeat sous 2s
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if self.running and self.last_heartbeat:
                    break
                if self._loop_task.done():
                    break
                await asyncio.sleep(0.05)

            if self.running:
                logger.info("[screen] started (%s)", self._status_detail)
                return {
                    "ok": True,
                    "service": "screen_watcher",
                    "status": "running",
                    "message": "Screen Watcher démarré",
                    "ollama": "healthy" if require_ollama else "skipped",
                    "vision_model": self.ollama_model,
                    **{k: v for k, v in self.status_payload().items() if k not in ("service", "status")},
                }

            self._status = self._status if self._status in SCREEN_WATCHER_STATES else "error"
            return {
                "ok": False,
                "service": "screen_watcher",
                "status": self.status,
                "message": self._status_detail or "Échec démarrage Screen Watcher",
                **self.status_payload(),
            }

    async def start(self) -> None:
        """Boucle principale du screen watcher."""
        if not self.enabled:
            self._status = "disabled"
            logger.info("[screen] désactivé (SCREEN_WATCHER_ENABLED=false)")
            return

        if self.running:
            logger.info("[screen] déjà actif — démarrage ignoré")
            return

        self.running = True
        self._status = "running"
        self.last_heartbeat = time.time()
        logger.info(
            "[screen] démarré — interval=%ss, seuils=%s%%/%s%%, max_width=%spx",
            self.interval, self.change_threshold, self.analysis_threshold, self.max_analysis_width,
        )

        # Détection unique de la résolution logique de l'écran (scale factor retina)
        await self._detect_screen_point_dimensions()

        try:
            while self.running:
                self.last_heartbeat = time.time()
                try:
                    await self._tick()
                except Exception as e:
                    self.error_count += 1
                    self._status = "error"
                    self._status_detail = str(e)
                    logger.exception("[screen] erreur tick : %s", e)
                await asyncio.sleep(self.interval)
        finally:
            self.running = False
            if self._status not in ("disabled", "blocked_ollama"):
                self._status = "stopped"
            self.last_image = None
            logger.info("[screen] stopped")

    async def stop_async(self, *, reason: str | None = None) -> dict:
        """Arrêt propre de la boucle (ne touche pas SCREEN_WATCHER_ENABLED)."""
        logger.info("[screen] stop requested%s", f" ({reason})" if reason else "")
        self._status = "stopping"
        self._status_detail = reason
        self.running = False
        self.defer_for_voice()
        task = self._loop_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        self._loop_task = None
        self.running = False
        self._status = "disabled" if not self.enabled else "stopped"
        return {
            "ok": True,
            "service": "screen_watcher",
            "status": self.status,
            "message": "Screen Watcher arrêté",
            **self.status_payload(),
        }

    def stop(self) -> None:
        """Arrêt synchrone (compat daemon) — lance le flag, annule la vision."""
        self.running = False
        if self._status not in ("disabled",):
            self._status = "stopping"
        self.defer_for_voice()

    def defer_for_voice(self) -> None:
        """Annule uniquement l'analyse vision active pour libérer les ressources."""
        task = self._vision_task
        if task is not None and not task.done():
            task.cancel()

    # ── Tick principal ──────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Un cycle de capture + crop + analyse."""
        if self._status == "error":
            self._status = "running"

        # 1. Capture écran complet (sans resize)
        img, tmp_path = await self._capture()
        if img is None:
            return
        self.last_capture_at = time.time()

        # 2. Fenêtre active (app + bounds)
        window_info = await self._get_active_window_info()
        current_app = window_info.get("app") if window_info else None

        # 3. Crop sur la fenêtre active + resize proportionnel
        cropped = self._crop_active_window(img, window_info)

        # 4. Nettoyage du fichier temporaire de capture
        self._cleanup_file(tmp_path)

        # 5. Hash + diff sur le CROP
        current_hash = self._hash_image(cropped)
        if current_hash == self.last_hash:
            self.idle_seconds += self.interval
            await self._check_idle()
            return

        change_pct = self._compute_diff(cropped, self.last_image) if self.last_image else 100.0
        self.last_image = cropped
        self.last_hash = current_hash

        # 6. Tracker app usage — chaque cycle (pas seulement sur changement d'app)
        if self.last_app:
            duration = int(time.time() - self.last_app_time)
            if duration > 0:
                upsert_app_usage(self.device, self.last_app, duration)
        self.last_app = current_app
        self.last_app_time = time.time()

        # 7. Peu de changement → idle
        if change_pct < self.change_threshold:
            self.idle_seconds += self.interval
            await self._check_idle()
            return

        # Réinitialiser compteur idle quand il y a du mouvement
        self.idle_seconds = 0
        self.idle_alerted = False

        # 8. Analyse Ollama si changement significatif (différée pendant conversation vocale)
        if change_pct >= self.analysis_threshold:
            if self._is_voice_busy():
                now = time.time()
                if now - self._vision_deferred_logged > 30:
                    self._vision_deferred_logged = now
                    logger.info("[screen] Analyse vision différée — STT/TTS prioritaires")
                return
            self._vision_task = asyncio.create_task(
                self._analyze_with_ollama(cropped, current_app, window_info),
                name="screen_vision",
            )
            try:
                analysis = await self._vision_task
            except asyncio.CancelledError:
                logger.info("[screen] Analyse vision annulée — priorité voix")
                return
            finally:
                self._vision_task = None
            if analysis:
                self.last_analysis_at = time.time()
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
            # 9. Changement mineur — sauvegarde basique
            save_screen_activity(
                device=self.device,
                app=current_app,
                activity="minor_change",
                screenshot_hash=current_hash,
                change_pct=round(change_pct, 1),
            )

    # ── Capture écran ───────────────────────────────────────────────────────

    async def _capture(self) -> tuple[Image.Image | None, str | None]:
        """Capture l'écran en résolution native via `screencapture` macOS.

        Returns:
            Tuple (image PIL brute, chemin du fichier temporaire).
            (None, None) si échec.
        """
        import os as _os

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "/usr/sbin/screencapture", "-x", "-C", "-t", "png", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=self.capture_timeout)

            # Attendre que le fichier soit complètement écrit (race condition macOS)
            await asyncio.sleep(0.1)

            if _os.path.getsize(tmp_path) < 500:
                raise IOError(f"capture vide ({_os.path.getsize(tmp_path)} octets)")

            img = Image.open(tmp_path)
            img.load()
            return img, tmp_path
        except Exception as e:
            logger.warning("[screen] capture échouée : %s", e)
            self._cleanup_file(tmp_path)
            return None, None

    # ── Fenêtre active ──────────────────────────────────────────────────────

    async def _get_active_window_info(self) -> dict | None:
        """osascript : récupère le nom de l'app et les bounds de la fenêtre active.

        Fallback vers _get_frontmost_app() (app seule, sans bounds) si échec.

        Retourne un dict {'app': str, 'x': int, 'y': int, 'width': int, 'height': int}
        ou None si rien n'a été récupéré.
        """
        try:
            script = (
                'tell application "System Events"\n'
                '  set frontProc to first application process whose frontmost is true\n'
                '  set appName to name of frontProc\n'
                '  try\n'
                '    set winPos to position of window 1 of frontProc\n'
                '    set winSize to size of window 1 of frontProc\n'
                '    return appName & "|" & (item 1 of winPos) & "|" & (item 2 of winPos)'
                ' & "|" & (item 1 of winSize) & "|" & (item 2 of winSize)\n'
                '  on error\n'
                '    return appName & "|0|0|0|0"\n'
                '  end try\n'
                'end tell'
            )
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.osascript_timeout)
            text = (stdout or b"").decode().strip()

            if not text:
                return await self._get_frontmost_app_or_none()

            parts = text.split("|")
            # Gérer le cas du on error osascript : "App||0|0|0|0" → 6 parties
            if len(parts) == 6 and parts[1] == "":
                parts = [parts[0]] + parts[2:]
            if len(parts) == 5:
                try:
                    return {
                        "app": parts[0] or None,
                        "x": int(parts[1]) if parts[1] else 0,
                        "y": int(parts[2]) if parts[2] else 0,
                        "width": int(parts[3]) if parts[3] else 0,
                        "height": int(parts[4]) if parts[4] else 0,
                    }
                except (ValueError, TypeError):
                    pass
            # Fallback : osascript a retourné seulement le nom de l'app
            return {"app": text, "x": 0, "y": 0, "width": 0, "height": 0}
        except asyncio.TimeoutError:
            logger.debug("[screen] osascript timeout — fallback frontmost app")
            return await self._get_frontmost_app_or_none()
        except Exception as e:
            logger.debug("[screen] _get_active_window_info : %s", e)
            return await self._get_frontmost_app_or_none()

    async def _get_frontmost_app(self) -> str | None:
        """Nom de l'app au premier plan (AppleScript)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to return name of first application process whose frontmost is true',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.osascript_timeout)
            name = (stdout or b"").decode().strip()
            return name or None
        except Exception:
            return None

    async def _get_frontmost_app_or_none(self) -> dict | None:
        """Retourne un dict contenant uniquement l'app, sans bounds."""
        name = await self._get_frontmost_app()
        if name:
            return {"app": name, "x": 0, "y": 0, "width": 0, "height": 0}
        return None

    # ── Crop + resize proportionnel ────────────────────────────────────────

    def _crop_active_window(self, img: Image.Image, window_info: dict | None) -> Image.Image:
        """Crop l'image sur la fenêtre active, avec resize proportionnel.

        - Détecte le scale factor retina via _screen_point_width.
        - Convertit les bounds (points) → pixels.
        - Recadre l'image sur ces coordonnées.
        - Si le crop dépasse MAX_ANALYSIS_WIDTH en largeur, resize proportionnel.
        - Si les bounds sont invalides (width=0), resize proportionnel de l'image entière.

        Args:
            img: Image PIL brute (capture native non redimensionnée).
            window_info: Dict de bounds (x, y, width, height) ou None.

        Returns:
            Image PIL croppée et éventuellement redimensionnée.
        """
        if window_info and window_info.get("width", 0) > 0 and window_info.get("height", 0) > 0:
            # Détecter le scale factor retina
            scale_factor: float = 1.0
            if self._screen_point_width > 0 and img.width > 0:
                scale_factor = img.width / self._screen_point_width

            # Convertir les bounds (points) → pixels
            x = int(window_info["x"] * scale_factor)
            y = int(window_info["y"] * scale_factor)
            w = int(window_info["width"] * scale_factor)
            h = int(window_info["height"] * scale_factor)

            # Borner aux dimensions de l'image
            x = max(0, min(x, img.width - 1))
            y = max(0, min(y, img.height - 1))
            w = max(1, min(w, img.width - x))
            h = max(1, min(h, img.height - y))

            cropped = img.crop((x, y, x + w, y + h))
        else:
            # Pas de bounds valides → image entière
            cropped = img

        # Resize proportionnel si > MAX_ANALYSIS_WIDTH
        if cropped.width > self.max_analysis_width:
            ratio = self.max_analysis_width / cropped.width
            new_height = max(1, int(cropped.height * ratio))
            cropped = cropped.resize((self.max_analysis_width, new_height), Image.Resampling.LANCZOS)

        return cropped

    # ── Diff pixel ──────────────────────────────────────────────────────────

    def _compute_diff(self, img1: Image.Image, img2: Image.Image) -> float:
        """% de pixels qui ont changé (>30 niveaux) entre deux images.

        Utilise des thumbnails 64×64 en niveaux de gris pour la comparaison.
        Si les tailles diffèrent → changement de fenêtre → return 100.0.
        """
        try:
            if img1.size != img2.size:
                return 100.0
            small1 = img1.resize((64, 64)).convert("L")
            small2 = img2.resize((64, 64)).convert("L")
            data1 = small1.getdata()
            data2 = small2.getdata()
            total = len(data1)
            if total == 0:
                return 100.0
            changed = sum(1 for a, b in zip(data1, data2) if abs(a - b) > 30)
            return (changed / total) * 100
        except Exception:
            return 100.0

    # ── Hash ────────────────────────────────────────────────────────────────

    def _hash_image(self, img: Image.Image) -> str:
        """Hash MD5 d'une vignette 64×64 en niveaux de gris."""
        small = img.resize((64, 64)).convert("L")
        return hashlib.md5(small.tobytes()).hexdigest()

    # ── Analyse Ollama ─────────────────────────────────────────────────────

    async def _analyze_with_ollama(
        self, img: Image.Image, app: str | None, window_info: dict | None
    ) -> dict | None:
        """Analyse l'image avec le LLM vision local (Ollama).

        Le prompt force une sortie JSON ultra-courte. Le résultat est texte pur
        — il ne quitte JAMAIS la machine. Coût Claude API = 0.

        Anti-spam : après N échecs consécutifs, désactive Ollama temporairement
        (cooldown de 5 min) pour éviter le flood de logs. Réessaie automatiquement.

        Args:
            img: Image PIL croppée (fenêtre active).
            app: Nom de l'application au premier plan.
            window_info: Dict de bounds (pour contexte dans le prompt).

        Returns:
            Dict JSON parsé ou None si échec.
        """
        if self._is_voice_busy():
            return None

        # Cooldown : vérifier si on peut réessayer
        if not self._ollama_available:
            if time.time() < self._ollama_next_retry:
                return None  # silencieux, pas de log
            # Fin du cooldown → retenter
            logger.info("[screen] Re-tentative Ollama après cooldown")
            self._ollama_available = True
            self._ollama_failures = 0

        # Anti-RAM-kill : espacement minimum entre analyses vision.
        # Chaque appel charge ~5 Go (qwen2.5-vl:7b) en RAM. Sans ce delai,
        # 618 appels en 6h saturent les 16 Go du Mac Mini → OOM kill macOS.
        now = time.time()
        if self._last_ollama_call > 0:
            since_last = now - self._last_ollama_call
            if since_last < self._ollama_min_interval_s:
                logger.debug("[screen] Ollama skip — dernier appel il y a %.0fs (min=%ds)",
                             since_last, int(self._ollama_min_interval_s))
                return None

        try:
            buffer = BytesIO()
            img_to_save = img
            if img_to_save.mode == "RGBA":
                img_to_save = img_to_save.convert("RGB")
            img_to_save.save(buffer, format="JPEG", quality=self.jpeg_quality)
            img_b64 = base64.b64encode(buffer.getvalue()).decode()

            win_w = window_info.get("width", 0) if window_info else 0
            win_h = window_info.get("height", 0) if window_info else 0
            app_label = app or "inconnue"

            prompt = (
                "Décris en 1 ligne ce que tu vois sur cet écran.\n"
                f"Application active : {app_label}. "
                f"Dimensions de la fenêtre : {win_w}x{win_h} points. "
                "L'écran complet fait 5120×1440 (ultrawide 32:9). "
                "L'image montrée est uniquement la fenêtre active, croppée.\n"
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
                        "keep_alive": "30s",
                        "options": {
                            "temperature": 0.1,
                            "num_predict": 100,
                        },
                    },
                )
                response.raise_for_status()
                result = response.json()

            # Succès → reset le compteur d'échecs + timestamp anti-RAM-kill
            self._ollama_failures = 0
            self._last_ollama_call = time.time()

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
                    "[screen] Ollama indisponible (%d échecs) — désactivé pour %ds",
                    self._ollama_failures, int(self._ollama_cooldown_s),
                )
            else:
                logger.debug("[screen] analyse Ollama échouée (%d/%d) : %s",
                           self._ollama_failures, self._ollama_max_failures, e)
            return None

    # ── Idle ────────────────────────────────────────────────────────────────

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

    # ── Screen point dimensions ────────────────────────────────────────────

    async def _detect_screen_point_dimensions(self) -> None:
        """Détecte la résolution logique de l'écran principal (points) pour le
        calcul du scale factor retina.

        Utilise : osascript → bounds of window of desktop (item 3 = width, item 4 = height).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to return (item 3 of bounds of window of desktop) & "|" & (item 4 of bounds of window of desktop)',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.osascript_timeout)
            text = (stdout or b"").decode().strip()
            parts = text.split("|")
            if len(parts) == 2:
                self._screen_point_width = int(parts[0])
                self._screen_point_height = int(parts[1])
                logger.info(
                    "[screen] résolution logique détectée : %dx%d points",
                    self._screen_point_width, self._screen_point_height,
                )
        except Exception as e:
            logger.debug("[screen] détection résolution logique échouée : %s", e)
            self._screen_point_width = 0
            self._screen_point_height = 0

    # ── Utilitaire ─────────────────────────────────────────────────────────

    @staticmethod
    def _cleanup_file(path: str | None) -> None:
        """Supprime silencieusement un fichier temporaire."""
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


screen_watcher = ScreenWatcher()
