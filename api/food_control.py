"""Pilotage complet d'Uber Eats depuis le tableau de bord.

Trois familles d'opérations, toutes derrière le verrou de session :

- **Composer un panier libre** en deux passes. La première construit le panier
  et lit le total réel sans rien engager ; la seconde consomme le plan opaque.
  C'est le même mécanisme que la commande vocale ou par chat : l'interface web
  n'obtient pas un raccourci vers le paiement, elle emprunte le chemin normal.
- **Inspecter et corriger l'installation** : état des sélecteurs, validité de
  la session, déclenchement d'une capture. Sans cela, réparer l'intégration
  imposerait un accès au terminal du Mac.
- **Lancer la capture de session**, qui ouvre une fenêtre de navigateur *sur la
  machine où tourne JARVIS*. À distance, on peut la déclencher et suivre son
  état, mais la connexion elle-même reste un geste humain devant l'écran.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from integrations.uber_eats import (
    UberEatsError,
    UberEatsLimitExceeded,
    UberEatsPlanError,
    UberEatsUnavailable,
    get_order_plan,
    revoke_order_plan,
    uber_eats,
)
from integrations.uber_eats_selectors import (
    KNOWN_ROLES,
    OPTIONAL_ROLES,
    REQUIRED_ROLES,
    SelectorConfigError,
    clear_selector_cache,
    load_selector_map,
)

logger = logging.getLogger("jarvis.food")

CAPTURE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "uber_eats_capture_session.py"
#: Au-delà, une capture laissée ouverte est considérée comme abandonnée.
CAPTURE_TIMEOUT_SECONDS = 900


class FoodControlError(RuntimeError):
    """Opération de pilotage refusée ; ``status_code`` porte la réponse HTTP."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Panier libre en deux passes ─────────────────────────────────────────────


async def prepare_manual_order(restaurant: object, items: object) -> dict[str, Any]:
    """Construit un panier et retourne le total réel, sans rien engager.

    Returns:
        La vue publique du plan : articles, total lu à l'écran, délai avant
        expiration. Le paiement exige un second appel explicite.

    Raises:
        FoodControlError: demande malformée, intégration indisponible, plafond
            atteint ou parcours navigateur en échec.
    """
    try:
        plan, _ = await uber_eats.prepare_order(restaurant, items)
    except UberEatsUnavailable as exc:
        raise FoodControlError(str(exc), status_code=503) from exc
    except UberEatsLimitExceeded as exc:
        raise FoodControlError(str(exc), status_code=409) from exc
    except UberEatsError as exc:
        raise FoodControlError(str(exc), status_code=400) from exc

    view = plan.public_view()
    view["needs_confirmation"] = True
    return view


async def confirm_manual_order(plan_id: object) -> dict[str, Any]:
    """Consomme un plan et passe la commande.

    Raises:
        FoodControlError: plan inconnu, expiré, déjà consommé, ou échec du
            parcours de paiement.
    """
    identifier = str(plan_id or "").strip()
    if not identifier:
        raise FoodControlError("Identifiant de panier manquant.", status_code=400)
    try:
        outcome = await uber_eats.confirm_order(identifier)
    except UberEatsPlanError as exc:
        raise FoodControlError(str(exc), status_code=409) from exc
    except UberEatsError as exc:
        raise FoodControlError(str(exc), status_code=502) from exc

    data = outcome.as_dict()
    # `plan_id` ne ressort pas : il est consommé, le renvoyer n'aurait d'usage
    # que pour tenter un rejeu.
    data.pop("plan_id", None)
    return data


def cancel_manual_order(plan_id: object) -> dict[str, Any]:
    """Révoque un panier en attente. Idempotent."""
    identifier = str(plan_id or "").strip()
    if not identifier:
        raise FoodControlError("Identifiant de panier manquant.", status_code=400)
    return {"ok": True, "revoked": revoke_order_plan(identifier)}


def peek_manual_order(plan_id: object) -> dict[str, Any]:
    """Relit un panier en attente sans le consommer.

    Raises:
        FoodControlError: plan inconnu, expiré ou déjà confirmé.
    """
    try:
        return get_order_plan(str(plan_id or ""))
    except UberEatsPlanError as exc:
        raise FoodControlError(str(exc), status_code=404) from exc


# ── Diagnostic de l'installation ────────────────────────────────────────────


def selectors_report() -> dict[str, Any]:
    """Décrit le fichier de sélecteurs : validité, couverture, fraîcheur."""
    path = Path(getattr(config, "UBER_EATS_SELECTORS_FILE", ""))
    try:
        selector_map = load_selector_map(path)
    except SelectorConfigError as exc:
        return {
            "ok": False,
            "path": str(path),
            "error": str(exc),
            "verified": False,
            "roles": {},
            "missing_required": sorted(REQUIRED_ROLES),
        }

    roles = {
        role: len(selector_map.strategies.get(role, ()))
        for role in sorted(KNOWN_ROLES)
    }
    return {
        "ok": True,
        "path": str(path),
        "verified": selector_map.verified,
        "captured_at": selector_map.captured_at,
        "version": selector_map.version,
        "roles": roles,
        "missing_required": [
            role for role in REQUIRED_ROLES if not selector_map.has(role)
        ],
        "missing_optional": [
            role for role in OPTIONAL_ROLES if not selector_map.has(role)
        ],
    }


def reload_selectors() -> dict[str, Any]:
    """Vide le cache et relit le fichier — utile après une recapture manuelle."""
    clear_selector_cache()
    return selectors_report()


def session_report() -> dict[str, Any]:
    """Décrit le fichier de session sans jamais en exposer le contenu."""
    path = uber_eats.storage_state_path
    exists = path.is_file() and not path.is_symlink()
    report: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "readable": uber_eats._session_state_readable(),
        "age_hours": None,
        "capture": capture_status(),
    }
    if exists:
        try:
            age = (time.time() - path.stat().st_mtime) / 3600.0
            report["age_hours"] = round(age, 1)
        except OSError as exc:
            logger.debug("[food] âge de session illisible : %s", exc)
    return report


async def probe_session() -> dict[str, Any]:
    """Ouvre une page headless et vérifie que la session est encore valide.

    Contrairement à ``session_report``, qui ne fait que lire un fichier, cette
    sonde répond à la seule question utile avant de commander : Uber nous
    reconnaît-il toujours ?
    """
    try:
        async with uber_eats.authenticated_page() as (page, _selectors):
            return {"ok": True, "url": page.url, "message": "Session valide."}
    except UberEatsError as exc:
        return {"ok": False, "message": str(exc)}


# ── Capture de session pilotée ──────────────────────────────────────────────


@dataclass
class CaptureJob:
    """Suivi de la capture en cours, unique par instance de JARVIS."""

    started_at: float = 0.0
    finished_at: float = 0.0
    returncode: int | None = None
    mode: str = "session"
    output: str = ""
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        """Vrai tant que le processus de capture est vivant."""
        return self.process is not None and self.process.returncode is None


_capture = CaptureJob()
_capture_lock = asyncio.Lock()


def capture_status() -> dict[str, Any]:
    """État de la dernière capture déclenchée depuis l'interface."""
    return {
        "running": _capture.running,
        "mode": _capture.mode,
        "started_at": _capture.started_at or None,
        "finished_at": _capture.finished_at or None,
        "returncode": _capture.returncode,
        "output": _capture.output[-2_000:],
    }


async def start_capture(mode: str = "session") -> dict[str, Any]:
    """Lance la capture de session sur la machine hôte.

    La fenêtre de navigateur s'ouvre sur le Mac qui exécute JARVIS : depuis un
    autre appareil, on déclenche et on observe, mais quelqu'un doit se
    connecter physiquement. Aucun identifiant ne transite par JARVIS.

    Args:
        mode: ``session`` pour la capture simple, ``codegen`` pour enregistrer
            en plus les sélecteurs réels.

    Raises:
        FoodControlError: mode inconnu, ou capture déjà en cours.
    """
    if mode not in ("session", "codegen"):
        raise FoodControlError(
            f"Mode de capture inconnu : {mode!r} (attendu 'session' ou 'codegen')."
        )
    if not CAPTURE_SCRIPT.is_file():
        raise FoodControlError(
            f"Script de capture introuvable : {CAPTURE_SCRIPT}", status_code=500
        )

    async with _capture_lock:
        if _capture.running:
            raise FoodControlError(
                "Une capture est déjà en cours ; terminer la fenêtre ouverte sur le Mac.",
                status_code=409,
            )
        command = [sys.executable, str(CAPTURE_SCRIPT)]
        if mode == "codegen":
            command.append("--codegen")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(CAPTURE_SCRIPT.parent.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise FoodControlError(
                f"Capture impossible à lancer ({exc}).", status_code=500
            ) from exc

        _capture.process = process
        _capture.mode = mode
        _capture.started_at = time.time()
        _capture.finished_at = 0.0
        _capture.returncode = None
        _capture.output = ""

    asyncio.create_task(_await_capture(process))
    logger.info("[food] capture de session lancée (mode %s, pid %s)", mode, process.pid)
    return capture_status()


async def _await_capture(process: asyncio.subprocess.Process) -> None:
    """Collecte la sortie de la capture et met fin au suivi."""
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=CAPTURE_TIMEOUT_SECONDS
        )
        _capture.output = (stdout or b"").decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        _capture.output = (
            f"Capture interrompue après {CAPTURE_TIMEOUT_SECONDS // 60} minutes "
            "sans fermeture de la fenêtre."
        )
        with_process_kill(process)
    except OSError as exc:  # pragma: no cover - défaillance système
        _capture.output = f"Capture interrompue : {exc}"
    finally:
        _capture.returncode = process.returncode
        _capture.finished_at = time.time()
        clear_selector_cache()


def with_process_kill(process: asyncio.subprocess.Process) -> None:
    """Termine un processus de capture resté ouvert, sans lever."""
    try:
        process.kill()
    except (ProcessLookupError, OSError) as exc:  # pragma: no cover - course rare
        logger.debug("[food] capture déjà terminée : %s", exc)


async def stop_capture() -> dict[str, Any]:
    """Interrompt la capture en cours. Idempotent."""
    if _capture.process is not None and _capture.running:
        with_process_kill(_capture.process)
    return capture_status()


def reset_capture_for_tests() -> None:
    """Réinitialise le suivi de capture. Réservé aux tests."""
    _capture.process = None
    _capture.started_at = 0.0
    _capture.finished_at = 0.0
    _capture.returncode = None
    _capture.output = ""
