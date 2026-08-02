"""Module audio — STT local multi-moteurs, VAD, sortie et files.

La synthèse vocale ne vit plus ici : elle est derrière l'interface locale
``jarvis.audio.tts``. Importer ``audio`` ne charge donc aucun moteur TTS.
"""

from audio.stt_daemon import stt_daemon as stt

__all__ = ["stt"]
