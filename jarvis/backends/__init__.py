"""Backends LLM du package JARVIS.

Deux classes strictement séparées, sans héritage commun, pour rendre impossible
toute confusion de routage :

- ``LocalBackend``    : MLX-LM en subprocess, données privées d'Elias.
- ``DeepSeekBackend`` : API HTTP DeepSeek, données anonymisées uniquement.
"""

from jarvis.backends.deepseek import DeepSeekBackend
from jarvis.backends.local import LocalBackend

__all__ = ["LocalBackend", "DeepSeekBackend"]
