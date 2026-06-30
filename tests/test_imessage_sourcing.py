"""Tests pour la réactivation du sourcing iMessage (lecture seule).

Vérifie que :
  - L'envoi est bloqué tant que IMESSAGE_SEND_ENABLED=false
  - Le reader ne crashe pas quand il vérifie sa disponibilité
  - Le scan périodique retourne un entier sans erreur
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


def test_send_blocked_by_default():
    """L'envoi doit lever RuntimeError quand IMESSAGE_SEND_ENABLED est False."""
    import config
    from integrations import imessage

    # Forcer l'état désactivé
    config.IMESSAGE_SEND_ENABLED = False
    with pytest.raises(RuntimeError, match="IMESSAGE_SEND_ENABLED=false"):
        imessage.send_imessage_to_address("+33600000000", "test")


def test_send_message_internal_blocked():
    """La méthode interne _send_message doit aussi lever RuntimeError."""
    import config
    from integrations.imessage import IMessageBridge

    config.IMESSAGE_SEND_ENABLED = False
    bridge = IMessageBridge("+33600000000")
    with pytest.raises(RuntimeError, match="IMESSAGE_SEND_ENABLED=false"):
        bridge._send_message("test")


def test_reader_available():
    """is_available() ne doit pas crasher — retourne True ou False."""
    from integrations.imessage_reader import imessage_reader

    result = imessage_reader.is_available()
    assert result in (True, False)


@pytest.mark.asyncio
async def test_scan_runs_without_error():
    """scan_new_messages() retourne un int sans crasher."""
    from integrations.imessage_reader import imessage_reader

    if imessage_reader.is_available():
        count = imessage_reader.scan_new_messages()
        assert isinstance(count, int)


def test_start_polling_returns_immediately_when_send_disabled():
    """start_polling() doit retourner immédiatement si IMESSAGE_SEND_ENABLED=false."""
    import config
    from integrations.imessage import IMessageBridge

    config.IMESSAGE_SEND_ENABLED = False
    bridge = IMessageBridge("+33600000000")
    # Ne doit pas lever d'exception ni boucler indéfiniment
    result = asyncio.run(bridge.start_polling(interval=0.1))
    assert result is None


def test_imessage_sourcing_config_defaults():
    """Vérifie les valeurs par défaut des nouvelles variables de config."""
    import config

    assert hasattr(config, "IMESSAGE_SOURCING_ENABLED")
    assert hasattr(config, "IMESSAGE_SEND_ENABLED")
    assert hasattr(config, "IMESSAGE_SCAN_INTERVAL")
    # Par défaut : sourcing=true, send=false, interval=300
    assert config.IMESSAGE_SOURCING_ENABLED is True
    assert config.IMESSAGE_SEND_ENABLED is False
    assert config.IMESSAGE_SCAN_INTERVAL == 300
