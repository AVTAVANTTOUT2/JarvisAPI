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
  python jarvis_agent.py --server http://100.123.50.38:8081 --pairing-code 123456

Le jeton reçu au premier pairage est stocké en permissions 0600. Les
démarrages suivants ne nécessitent plus de secret sur la ligne de commande.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests
from PIL import Image


class JarvisAgent:
    def __init__(
        self,
        server_url: str,
        auth_token: str | None = None,
        device_id: str | None = None,
        *,
        pairing_code: str | None = None,
        token_file: Path | None = None,
    ) -> None:
        self.server = server_url.rstrip("/")
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

        token_filename = hashlib.sha256(self.device_id.encode("utf-8")).hexdigest()[:16] + ".token"
        self.token_file = token_file or (
            Path.home() / "Library" / "Application Support" / "JARVIS" / "device_tokens" / token_filename
        )
        self.pairing_code = (pairing_code or "").strip() or None
        self._token_from_cli = bool((auth_token or "").strip())
        self.token = (auth_token or "").strip() or self._load_token()
        self.headers: dict[str, str] = {}
        self._refresh_headers()
        self.last_hash: str | None = None
        self.last_image: Image.Image | None = None
        self.running = False

        print(f"[agent] device : {self.device_name} ({self.device_id})")
        print(f"[agent] server : {self.server}")

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.token:
            if not self.pairing_code:
                raise RuntimeError(
                    "Aucun jeton enregistré. Générez un code dans JARVIS puis relancez avec --pairing-code."
                )
            self._register(self.pairing_code)
        else:
            self._verify_credentials()
            if self._token_from_cli:
                self._save_token(self.token)

        self.running = True

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

    def _load_token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
            if token:
                os.chmod(self.token_file, 0o600)
            return token
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise RuntimeError(f"Lecture du jeton impossible : {exc}") from exc

    def _save_token(self, token: str) -> None:
        try:
            self.token_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(self.token_file.parent, 0o700)
            fd = os.open(
                self.token_file,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
                handle.write("\n")
            os.chmod(self.token_file, 0o600)
        except OSError as exc:
            raise RuntimeError(f"Persistance du jeton impossible : {exc}") from exc

    def _refresh_headers(self) -> None:
        self.headers = {"X-Device-Token": self.token} if self.token else {}

    def _register(self, pairing_code: str) -> None:
        try:
            response = requests.post(
                f"{self.server}/api/devices/register",
                json={
                    "device_id": self.device_id,
                    "device_name": self.device_name,
                    "device_type": "laptop",
                    "pairing_code": pairing_code,
                },
                timeout=10,
            )
            response.raise_for_status()
            token = str(response.json().get("token") or "").strip()
            if not token:
                raise RuntimeError("Le serveur n'a pas émis de jeton")
            self.token = token
            self._refresh_headers()
            self._save_token(token)
            self.pairing_code = None
            print("[agent] pairage terminé et jeton enregistré localement")
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                try:
                    detail = f" — {exc.response.json().get('detail', '')}"
                except (TypeError, ValueError):
                    detail = ""
            raise RuntimeError(f"Pairage refusé{detail}") from exc

    def _verify_credentials(self) -> None:
        try:
            response = requests.post(
                f"{self.server}/api/devices/{self.device_id}/heartbeat",
                headers=self.headers,
                timeout=5,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                "Jeton device invalide ou révoqué. Effectuez une rotation depuis JARVIS."
            ) from exc

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
            # SCREEN_RESIZE aligné sur config.SCREEN_RESIZE / scripts/screen_watcher.py (1280×800)
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
                            subprocess.run(
                                ["afplay", tmp_path],
                                capture_output=True,
                                timeout=30,
                            )
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
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument("--token", help="Jeton existant ou nouvellement tourné")
    credentials.add_argument(
        "--pairing-code",
        help="Code à six chiffres généré depuis une session JARVIS privée",
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="ID de la machine (défaut : LocalHostName)",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="Chemin de stockage 0600 du jeton (défaut : Application Support/JARVIS)",
    )
    args = parser.parse_args()

    agent = JarvisAgent(
        args.server,
        args.token,
        args.device_id,
        pairing_code=args.pairing_code,
        token_file=args.token_file,
    )
    agent.start()


if __name__ == "__main__":
    main()
