#!/usr/bin/env python3
"""Capture la session Uber Eats connectée, et assiste la capture des sélecteurs.

Étape obligatoire avant toute commande : JARVIS ne connaît pas les
identifiants Uber et ne les demandera jamais. L'utilisateur se connecte
lui-même dans une vraie fenêtre de navigateur — double authentification
comprise — et seul l'état de session résultant (cookies, stockage local) est
conservé, en 0600, pour être rejoué plus tard en mode headless.

Deux modes :

    python scripts/uber_eats_capture_session.py
        Ouvre un navigateur, attend la fermeture de la fenêtre, écrit la session.

    python scripts/uber_eats_capture_session.py --codegen
        Lance ``playwright codegen`` qui, en plus de la session, imprime le code
        des sélecteurs réellement utilisés. Ce sont ces sélecteurs qu'il faut
        reporter dans le fichier JSON, puis basculer ``"verified": true``.

Tant que ``"verified"`` vaut ``false``, l'intégration refuse de cliquer sur le
bouton de paiement, quels que soient les autres réglages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402  - le chemin projet doit être injecté avant l'import
from core.file_security import ensure_private_directory, write_private_bytes  # noqa: E402
from integrations.playwright_runtime import (  # noqa: E402
    PlaywrightUnavailable,
    import_playwright,
)
from integrations.uber_eats_selectors import (  # noqa: E402
    SelectorConfigError,
    load_selector_map,
)

logger = logging.getLogger("jarvis.uber_eats.capture")

EXIT_OK = 0
EXIT_PLAYWRIGHT_MISSING = 2
EXIT_CAPTURE_FAILED = 3
EXIT_CODEGEN_FAILED = 4


def _storage_path() -> Path:
    return Path(config.UBER_EATS_STORAGE_STATE)


def _write_storage_state(state: dict) -> Path:
    """Écrit l'état de session directement en 0600, sans fenêtre lisible."""
    destination = _storage_path()
    ensure_private_directory(destination.parent)
    payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    write_private_bytes(destination, payload)
    return destination


async def capture_session() -> Path:
    """Ouvre un navigateur visible et enregistre la session à sa fermeture.

    Raises:
        PlaywrightUnavailable: paquet Playwright absent.
        RuntimeError: la fenêtre a été fermée sans qu'aucune session soit lisible.
    """
    api = import_playwright()
    async with api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            locale=str(config.UBER_EATS_LOCALE),
            timezone_id=str(config.TIMEZONE),
        )
        page = await context.new_page()
        await page.goto(str(config.UBER_EATS_BASE_URL), wait_until="domcontentloaded")

        print(
            "\nConnecte-toi manuellement dans la fenêtre qui vient de s'ouvrir "
            "(double authentification comprise).\n"
            "Vérifie que tu es bien sur la page d'accueil connectée, puis ferme "
            "la fenêtre : la session sera enregistrée automatiquement.\n"
        )
        closed = asyncio.Event()
        page.on("close", lambda _page: closed.set())
        context.on("close", lambda _context: closed.set())
        await closed.wait()

        try:
            state = await context.storage_state()
        except api.Error as exc:
            await browser.close()
            raise RuntimeError(
                f"Session illisible après fermeture de la fenêtre : {exc}"
            ) from exc
        await browser.close()

    if not state.get("cookies"):
        raise RuntimeError(
            "Aucun cookie capturé : la connexion n'a probablement pas abouti. "
            "Relance la capture et vérifie que tu es connecté avant de fermer."
        )
    return _write_storage_state(state)


def run_codegen() -> int:
    """Lance ``playwright codegen`` avec sauvegarde de session.

    Codegen écrit lui-même le fichier de session : on le pointe vers un fichier
    temporaire privé, puis on réécrit proprement en 0600.
    """
    destination = _storage_path()
    ensure_private_directory(destination.parent)
    temporary = destination.with_suffix(".codegen.json")
    command = [
        sys.executable,
        "-m",
        "playwright",
        "codegen",
        "--target",
        "python-async",
        f"--save-storage={temporary}",
        str(config.UBER_EATS_BASE_URL),
    ]
    print(
        "\nUne fenêtre codegen va s'ouvrir. Enchaîne le parcours complet :\n"
        "  1. connexion au compte\n"
        "  2. recherche d'un restaurant\n"
        "  3. ouverture d'un article, ajout au panier\n"
        "  4. ouverture du panier, écran de paiement\n"
        "  5. NE PAS valider la commande — fermer la fenêtre\n"
        "Le code affiché contient les sélecteurs réels à reporter dans "
        f"{config.UBER_EATS_SELECTORS_FILE}.\n"
    )
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"Lancement de codegen impossible : {exc}", file=sys.stderr)
        return EXIT_CODEGEN_FAILED

    if completed.returncode != EXIT_OK:
        print(
            f"codegen s'est terminé avec le code {completed.returncode}.",
            file=sys.stderr,
        )
        return EXIT_CODEGEN_FAILED

    if not temporary.is_file():
        print(
            f"codegen n'a produit aucune session dans {temporary}.",
            file=sys.stderr,
        )
        return EXIT_CODEGEN_FAILED

    try:
        state = json.loads(temporary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Session codegen illisible ({temporary}) : {exc}", file=sys.stderr)
        return EXIT_CODEGEN_FAILED
    finally:
        temporary.unlink(missing_ok=True)

    saved = _write_storage_state(state)
    print(f"Session enregistrée : {saved}")
    _report_selector_state()
    return EXIT_OK


def _report_selector_state() -> None:
    """Rappelle où en est le fichier de sélecteurs après une capture."""
    path = Path(config.UBER_EATS_SELECTORS_FILE)
    try:
        selector_map = load_selector_map(path)
    except SelectorConfigError as exc:
        print(f"\nFichier de sélecteurs inutilisable : {exc}", file=sys.stderr)
        return
    if selector_map.verified:
        print(f"\nSélecteurs marqués vérifiés dans {path}.")
        return
    print(
        f"\nSélecteurs NON vérifiés dans {path}.\n"
        "Reporte les sélecteurs réels affichés par codegen, puis passe "
        '"verified": true. Tant que ce drapeau est faux, JARVIS construit le '
        "panier en simulation mais ne clique jamais sur le bouton de paiement."
    )


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée en ligne de commande."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Capture la session Uber Eats utilisée par JARVIS."
    )
    parser.add_argument(
        "--codegen",
        action="store_true",
        help="lance playwright codegen pour capturer aussi les sélecteurs réels",
    )
    args = parser.parse_args(argv)

    try:
        import_playwright()
    except PlaywrightUnavailable as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_PLAYWRIGHT_MISSING

    if args.codegen:
        return run_codegen()

    try:
        saved = asyncio.run(capture_session())
    except PlaywrightUnavailable as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_PLAYWRIGHT_MISSING
    except (RuntimeError, OSError) as exc:
        print(f"Capture échouée : {exc}", file=sys.stderr)
        return EXIT_CAPTURE_FAILED

    print(f"Session enregistrée : {saved}")
    _report_selector_state()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
