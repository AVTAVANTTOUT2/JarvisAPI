"""Agent JARVIS pour machine distante (MacBook Pro, iMac…).

Script autonome — un seul fichier, dépendances minimales (`requests`, `Pillow`).
Capture l'écran, envoie périodiquement les screenshots au Mac Mini via Tailscale,
joue l'audio TTS reçu en retour.

Tout le traitement (Ollama vision local, triage, Claude API, mémoire) tourne
sur le Mac Mini. Cet agent ne fait QUE :

  1. capturer son écran ;
  2. comparer avec le précédent (hash + diff pixel) ;
  3. envoyer le screenshot au serveur si changement significatif ;
  4. poller la file TTS et jouer l'audio reçu.

Usage :
  pip install -r requirements-agent.txt
  python jarvis_agent.py --server http://100.123.50.38:8081 --token MON_TOKEN
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests
from PIL import Image


class JarvisAgent:
    def __init__(self, server_url: str, auth_token: str, device_id: str | None = None) -> None:
        self.server = server_url.rstrip("/")
        self.token = auth_token
        try:
            default_id = (
                subprocess.check_output(["scutil", "--get", "LocalHostName"])
                .decode()
                .strip()
            )
        except Exception:
            default_id = "unknown"
        self.device_id = device_id or default_id
        try:
            self.device_name = (
                subprocess.check_output(["scutil", "--get", "ComputerName"])
                .decode()
                .strip()
            )
        except Exception:
            self.device_name = self.device_id

        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.last_hash: str | None = None
        self.last_image: Image.Image | None = None
        self.running = False

        print(f"[agent] device : {self.device_name} ({self.device_id})")
        print(f"[agent] server : {self.server}")

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self.running = True
        self._register()

        threads = [
            threading.Thread(target=self._heartbeat_loop, daemon=True),
            threading.Thread(target=self._screen_loop, daemon=True),
            threading.Thread(target=self._tts_poll_loop, daemon=True),
        ]
        for t in threads:
            t.start()

        print("[agent] démarré. Ctrl+C pour arrêter.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[agent] arrêt…")
            self.running = False

    # ── Enregistrement ────────────────────────────────────────────────────────

    def _register(self) -> None:
        try:
            r = requests.post(
                f"{self.server}/api/devices/register",
                json={
                    "device_id": self.device_id,
                    "device_name": self.device_name,
                    "device_type": "laptop",
                },
                headers=self.headers,
                timeout=10,
            )
            print(f"[agent] enregistré : {r.json()}")
        except Exception as e:
            print(f"[agent] erreur enregistrement : {e}")

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        while self.running:
            try:
                requests.post(
                    f"{self.server}/api/devices/{self.device_id}/heartbeat",
                    headers=self.headers,
                    timeout=5,
                )
            except Exception:
                pass
            time.sleep(30)

    # ── Capture + envoi screenshot ────────────────────────────────────────────

    def _screen_loop(self) -> None:
        while self.running:
            try:
                img = self._capture()
                if img is None:
                    time.sleep(12)
                    continue

                small = img.resize((64, 64)).convert("L")
                current_hash = hashlib.md5(small.tobytes()).hexdigest()

                if current_hash == self.last_hash:
                    time.sleep(12)
                    continue

                change_pct = self._compute_diff(img, self.last_image) if self.last_image else 100.0
                self.last_hash = current_hash
                self.last_image = img

                if change_pct < 5:
                    time.sleep(12)
                    continue

                if change_pct >= 15:
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=60)
                    img_b64 = base64.b64encode(buffer.getvalue()).decode()

                    try:
                        app = subprocess.check_output(
                            [
                                "osascript",
                                "-e",
                                'tell application "System Events" to return name of first application process whose frontmost is true',
                            ],
                            timeout=3,
                        ).decode().strip()
                    except Exception:
                        app = "unknown"

                    try:
                        requests.post(
                            f"{self.server}/api/devices/{self.device_id}/screen",
                            json={
                                "image_b64": img_b64,
                                "app": app,
                                "change_pct": round(change_pct, 1),
                            },
                            headers=self.headers,
                            timeout=15,
                        )
                    except Exception as e:
                        print(f"[agent] envoi screen : {e}")
            except Exception as e:
                print(f"[agent] screen erreur : {e}")
            time.sleep(12)

    def _capture(self) -> Image.Image | None:
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-C", "-t", "png", tmp_path],
                capture_output=True,
                timeout=5,
            )
            img = Image.open(tmp_path)
            img.load()
            img = img.resize((1280, 800), Image.Resampling.LANCZOS)
            return img
        except Exception as e:
            print(f"[agent] capture : {e}")
            return None
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    @staticmethod
    def _compute_diff(img1: Image.Image, img2: Image.Image) -> float:
        try:
            if img1.size != img2.size:
                img2 = img2.resize(img1.size)
            g1 = list(img1.convert("L").getdata())
            g2 = list(img2.convert("L").getdata())
            total = len(g1)
            if total == 0:
                return 100.0
            changed = sum(1 for a, b in zip(g1, g2) if abs(a - b) > 30)
            return (changed / total) * 100
        except Exception:
            return 100.0

    # ── Polling TTS ───────────────────────────────────────────────────────────

    def _tts_poll_loop(self) -> None:
        while self.running:
            try:
                r = requests.get(
                    f"{self.server}/api/devices/{self.device_id}/tts",
                    headers=self.headers,
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json()
                    audio_b64 = data.get("audio_b64")
                    if audio_b64:
                        audio = base64.b64decode(audio_b64)
                        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                            tmp.write(audio)
                            tmp_path = tmp.name
                        try:
                            subprocess.run(["afplay", tmp_path], capture_output=True)
                        finally:
                            try:
                                Path(tmp_path).unlink(missing_ok=True)
                            except Exception:
                                pass
            except Exception:
                pass
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent JARVIS pour machine distante")
    parser.add_argument(
        "--server",
        required=True,
        help="URL du serveur JARVIS (ex: http://100.123.50.38:8081)",
    )
    parser.add_argument("--token", required=True, help="Token d'authentification")
    parser.add_argument(
        "--device-id",
        default=None,
        help="ID de la machine (défaut : LocalHostName)",
    )
    args = parser.parse_args()

    agent = JarvisAgent(args.server, args.token, args.device_id)
    agent.start()


if __name__ == "__main__":
    main()
