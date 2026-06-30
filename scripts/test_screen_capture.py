"""Script de diagnostic standalone — screen capture + crop ultrawide.

Usage :
  cd /Users/zeldris/JarvisAPI
  source venv/bin/activate
  python scripts/test_screen_capture.py

Vérifie :
  1. Permission screencapture + dimensions de l'image capturée.
  2. osascript : bounds de la fenêtre active.
  3. Capture complète → crop → resize proportionnel.
  4. Ollama : qwen2.5-vl pullé et disponible.
"""

import asyncio
import base64
import hashlib
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

# Ajouter la racine du projet au path pour les imports internes
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def hr(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


async def test_screencapture() -> bool:
    """Vérifie la permission screencapture + dimensions de l'image."""
    hr("1. Screencapture")
    try:
        from PIL import Image

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "/usr/sbin/screencapture", "-x", "-C", "-t", "png", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)

            if proc.returncode != 0:
                print(red("  ECHEC : screencapture a retourné le code %d" % proc.returncode))
                print(yellow("  → Vérifie la permission Enregistrement d'écran dans Réglages Système"))
                return False

            img = Image.open(tmp_path)
            img.load()
            print(green(f"  OK — image capturée : {img.width}x{img.height}px, mode={img.mode}"))
            return True
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as e:
        print(red(f"  ECHEC : {e}"))
        return False


async def test_active_window() -> bool:
    """Vérifie l'osascript pour bounds de fenêtre."""
    hr("2. Fenêtre active (osascript)")
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
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
        text = (stdout or b"").decode().strip()
        err = (stderr or b"").decode().strip()

        if err:
            print(yellow(f"  ATTENTION — stderr: {err}"))

        parts = text.split("|")
        if len(parts) == 5:
            app = parts[0]
            x, y, w, h = parts[1], parts[2], parts[3], parts[4]
            print(green(f"  OK — app: {app}, bounds: x={x} y={y} w={w} h={h}"))
            return True
        else:
            print(yellow(f"  ATTENTION — format inattendu: {text}"))
            return bool(text)
    except asyncio.TimeoutError:
        print(red("  ECHEC : osascript timeout"))
        return False
    except Exception as e:
        print(red(f"  ECHEC : {e}"))
        return False


async def test_crop() -> bool:
    """Capture complète + crop + resize proportionnel."""
    hr("3. Crop + resize proportionnel")
    try:
        from PIL import Image

        # Étape 1 : capture
        tmp_path = None
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
            print(f"  Capture brute : {img.width}x{img.height}px")

            # Étape 2 : détecter le scale factor
            sf_proc = await asyncio.create_subprocess_exec(
                "osascript", "-e",
                'tell application "System Events" to return (item 3 of bounds of window of desktop)',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            sf_stdout, _ = await asyncio.wait_for(sf_proc.communicate(), timeout=3)
            point_width = int((sf_stdout or b"0").decode().strip())
            scale_factor = img.width / point_width if point_width > 0 else 1.0
            print(f"  Résolution logique : {point_width} points → scale factor : {scale_factor:.1f}")

            # Étape 3 : récupérer les bounds de la fenêtre active
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
            win_proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            win_stdout, _ = await asyncio.wait_for(win_proc.communicate(), timeout=3)
            win_text = (win_stdout or b"").decode().strip()
            parts = win_text.split("|")

            if len(parts) == 5 and parts[1] and int(parts[1]) >= 0:
                app = parts[0]
                x = int(parts[1]) * scale_factor
                y = int(parts[2]) * scale_factor
                w = int(parts[3]) * scale_factor
                h = int(parts[4]) * scale_factor

                # Borner
                x = max(0, min(int(x), img.width - 1))
                y = max(0, min(int(y), img.height - 1))
                w = max(1, min(int(w), img.width - int(x)))
                h = max(1, min(int(h), img.height - int(y)))

                cropped = img.crop((int(x), int(y), int(x) + int(w), int(y) + int(h)))
                print(f"  Crop fenêtre '{app}' : {cropped.width}x{cropped.height}px")
            else:
                cropped = img
                print(yellow("  Pas de bounds valides — image entière utilisée"))

            # Étape 4 : resize proportionnel
            MAX_WIDTH = 1280
            if cropped.width > MAX_WIDTH:
                ratio = MAX_WIDTH / cropped.width
                new_h = max(1, int(cropped.height * ratio))
                cropped = cropped.resize((MAX_WIDTH, new_h), Image.Resampling.LANCZOS)
                print(green(f"  OK — resize proportionnel : {cropped.width}x{cropped.height}px (ratio {ratio:.2f})"))
            else:
                print(green(f"  OK — pas de resize nécessaire : {cropped.width}x{cropped.height}px"))

            return True
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as e:
        print(red(f"  ECHEC : {e}"))
        import traceback
        traceback.print_exc()
        return False


async def test_ollama() -> bool:
    """Vérifie si qwen2.5-vl est pullé."""
    hr("4. Ollama — qwen2.5-vl")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                print(red(f"  ECHEC : Ollama injoignable (HTTP {resp.status_code})"))
                print(yellow("  → Vérifie que Ollama tourne : ollama serve"))
                return False

            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            vl_models = [m for m in models if "vl" in m.lower() or "vision" in m.lower()]

            if vl_models:
                print(green(f"  OK — modèles vision disponibles : {', '.join(vl_models)}"))
                return True
            else:
                print(yellow(f"  ATTENTION — aucun modèle vision trouvé parmi {len(models)} modèles"))
                print(yellow(f"  Modèles présents : {', '.join(models[:10])}{'...' if len(models) > 10 else ''}"))
                print(yellow("  → Lance : ollama pull qwen2.5vl:7b"))
                return False
    except Exception as e:
        print(red(f"  ECHEC : {e}"))
        print(yellow("  → Vérifie que Ollama tourne sur http://localhost:11434"))
        return False


async def main() -> None:
    print("\n" + "=" * 60)
    print("  DIAGNOSTIC SCREEN CAPTURE — JARVIS")
    print("=" * 60)

    results: dict[str, bool] = {}

    results["screencapture"] = await test_screencapture()
    results["active_window"] = await test_active_window()
    if results["screencapture"]:
        results["crop"] = await test_crop()
    else:
        print(yellow("\n  → Test crop sauté (screencapture requis)"))
        results["crop"] = False
    results["ollama"] = await test_ollama()

    # Résumé
    print("\n" + "=" * 60)
    print("  RÉSUMÉ")
    print("=" * 60)
    for test_name, passed in results.items():
        status = green("PASS") if passed else red("FAIL")
        print(f"  {test_name:<25} {status}")

    all_pass = all(results.values())
    if all_pass:
        print(green("\n  Tous les tests OK — le screen watcher ultrawide est prêt.\n"))
    else:
        print(red("\n  Certains tests ont échoué — corrige avant de lancer le daemon.\n"))

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
