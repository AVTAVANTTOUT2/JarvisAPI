"""Module integrations — Mail (AppleScript), Calendar, Météo, iMessage.

Imports conditionnels : si une intégration échoue, elle est `None`
mais le serveur continue de tourner.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from integrations.mail import mail_client
except Exception as e:
    logger.warning(f"[integrations] Mail indisponible : {e}")
    mail_client = None

try:
    from integrations.calendar_api import calendar_client
except Exception as e:
    logger.warning(f"[integrations] Calendar indisponible : {e}")
    calendar_client = None

try:
    from integrations.weather import weather
except Exception as e:
    logger.warning(f"[integrations] Weather indisponible : {e}")
    weather = None

try:
    from integrations.computer import computer
except Exception as e:
    logger.warning(f"[integrations] computer indisponible : {e}")
    computer = None

try:
    from integrations.imessage import imessage_bridge
except Exception as e:
    logger.warning(f"[integrations] imessage indisponible : {e}")
    imessage_bridge = None

try:
    from integrations.contacts import contacts_reader
except Exception as e:
    logger.warning(f"[integrations] Contacts.app reader indisponible : {e}")
    contacts_reader = None

__all__ = ["mail_client", "calendar_client", "weather", "imessage_bridge", "computer", "contacts_reader"]
