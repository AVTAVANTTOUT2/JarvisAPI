"""Absence de micro : une précondition, pas un plantage.

Un Mac mini n'a pas de micro intégré ; débrancher un micro USB suffit à mettre
le daemon dans cet état. La version antérieure ouvrait un flux sur un
périphérique inexistant, mourait dans le thread pyaudio, et laissait la boucle
de relance recommencer toutes les trois secondes avec une trace complète à
chaque tour — jusqu'à 66 Mo de journal — pendant que l'interface affichait
« ERROR » sans dire pourquoi.

Trois propriétés sont figées ici :

1. l'absence de périphérique est détectée **avant** de démarrer quoi que ce soit ;
2. elle n'est pas comptée comme un crash et n'escalade pas le backoff ;
3. la cause voyage jusqu'à l'interface, au lieu de rester dans les journaux.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_missing_device_raises_a_dedicated_error():
    """Le résolveur qui rend ``None`` doit produire une erreur nommée.

    Le distinguer d'un `Exception` générique est ce qui permet à la boucle de
    relance de choisir « attendre » plutôt que « relancer ».
    """
    from scripts.audio_daemon import NoInputDeviceError

    assert issubclass(NoInputDeviceError, RuntimeError)
    source = inspect.getsource(
        __import__("scripts.audio_daemon", fromlist=["x"]).AudioDaemon._run
    )
    assert "_resolve_input_device_index" in source
    assert "NoInputDeviceError" in source
    # La vérification précède le lancement des boucles : sinon on démarre des
    # tâches qu'il faudra démonter juste après.
    assert source.index("NoInputDeviceError") < source.index("_vad_task")


def test_absence_is_not_counted_as_a_crash():
    """Ni compteur de crash, ni escalade de backoff, ni trace par tentative."""
    module = __import__("scripts.audio_daemon", fromlist=["x"])
    start_source = inspect.getsource(module.AudioDaemon.start)

    handler = start_source[start_source.index("except NoInputDeviceError"):]
    handler = handler[: handler.index("except Exception")]

    assert "consecutive_crashes" not in handler, (
        "l'absence de matériel ne doit pas alimenter le compteur de crashes"
    )
    assert "backoff_s" not in handler, (
        "relancer plus lentement ne fait pas apparaître un micro"
    )
    assert "exc_info" not in handler, (
        "une trace complète toutes les 20 s remplit le journal sans rien apprendre"
    )
    assert "NO_INPUT_DEVICE_POLL_S" in handler, "la reprise doit être automatique"
    assert module.NO_INPUT_DEVICE_POLL_S >= 5.0


def test_state_and_reason_are_both_published():
    """L'état nommé et sa cause partent ensemble vers l'interface."""
    module = __import__("scripts.audio_daemon", fromlist=["x"])
    start_source = inspect.getsource(module.AudioDaemon.start)
    status_source = inspect.getsource(module.AudioDaemon.get_status)

    assert '"no_input_device"' in start_source
    assert "_error_reason" in start_source
    assert '"error": self._error_reason' in status_source

    control = (PROJECT_ROOT / "api" / "service_control.py").read_text(encoding="utf-8")
    assert '"error": getattr(audio_daemon, "_error_reason", None)' in control, (
        "le plan de contrôle laisse tomber la cause : l'UI ne peut afficher "
        "que « ERROR »"
    )


def test_reason_is_cleared_when_the_daemon_starts():
    """Un micro rebranché ne doit pas garder l'ancienne explication."""
    module = __import__("scripts.audio_daemon", fromlist=["x"])
    start_source = inspect.getsource(module.AudioDaemon.start)
    assert "self._error_reason = None" in start_source


@pytest.mark.asyncio
async def test_daemon_waits_instead_of_spinning(monkeypatch):
    """Sans micro, la boucle attend et retente — sans jamais crasher.

    On force l'échec de précondition et on vérifie que la boucle survit à
    plusieurs tours sans lever, ce qui est exactement ce que l'ancienne version
    ne faisait pas.
    """
    module = __import__("scripts.audio_daemon", fromlist=["x"])
    daemon = module.AudioDaemon()

    attempts = 0

    async def fake_run(self=daemon):
        nonlocal attempts
        attempts += 1
        if attempts >= 3:
            self.enabled = False  # sortie propre après trois sondes
        raise module.NoInputDeviceError("aucun micro (test)")

    monkeypatch.setattr(module.AudioDaemon, "_run", fake_run)
    monkeypatch.setattr(module.AudioDaemon, "_cleanup", lambda self: None)
    monkeypatch.setattr(module, "NO_INPUT_DEVICE_POLL_S", 0.01)
    async def _no_warmup(self):
        return None

    monkeypatch.setattr(module.AudioDaemon, "_warmup_tts_pipeline", _no_warmup)
    monkeypatch.setattr(module, "_generate_wake_sound", lambda: None)
    monkeypatch.setattr(module, "_generate_end_sound", lambda: None)
    monkeypatch.setattr(
        module.AudioDaemon, "_schedule_state_broadcast", lambda self, s: None
    )

    await asyncio.wait_for(daemon.start(), timeout=5)

    assert attempts >= 3, "la boucle doit retenter, pas abandonner"
    assert daemon.state == "no_input_device"
    assert daemon._error_reason and "micro" in daemon._error_reason.lower()


@pytest.mark.asyncio
async def test_stop_during_the_wait_leaves_the_daemon_restartable(monkeypatch):
    """Arrêter le daemon pendant l'attente ne doit pas le condamner.

    L'attente sans micro repositionnait ``_running = True`` juste avant de
    reboucler. Si l'arrêt tombait pendant la sonde, la boucle sortait sur
    ``enabled = False`` en laissant ``_running`` à True — et ``start()``, qui
    répond « Déjà actif » dans ce cas, refusait ensuite tout redémarrage
    jusqu'au prochain lancement du processus.

    C'est l'état exact où l'utilisateur touche le plan de contrôle : le micro
    manque, donc il arrête et relance le service.
    """
    module = __import__("scripts.audio_daemon", fromlist=["x"])
    daemon = module.AudioDaemon()

    async def fake_run(self=daemon):
        raise module.NoInputDeviceError("aucun micro (test)")

    async def _no_warmup(self):
        return None

    monkeypatch.setattr(module.AudioDaemon, "_run", fake_run)
    monkeypatch.setattr(module.AudioDaemon, "_cleanup", lambda self: None)
    monkeypatch.setattr(module, "NO_INPUT_DEVICE_POLL_S", 0.05)
    monkeypatch.setattr(module.AudioDaemon, "_warmup_tts_pipeline", _no_warmup)
    monkeypatch.setattr(module, "_generate_wake_sound", lambda: None)
    monkeypatch.setattr(module, "_generate_end_sound", lambda: None)
    monkeypatch.setattr(
        module.AudioDaemon, "_schedule_state_broadcast", lambda self, s: None
    )

    loop_task = asyncio.create_task(daemon.start())
    await asyncio.sleep(0.02)          # la boucle est dans sa sonde

    daemon.enabled = False             # ce que fait stop() en premier
    daemon._running = False
    await asyncio.wait_for(loop_task, timeout=5)

    assert daemon._running is False, (
        "un daemon arrêté doit se déclarer arrêté, sinon start() refuse de le relancer"
    )
