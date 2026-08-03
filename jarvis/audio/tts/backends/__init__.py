"""Backends de synthèse locale.

Un seul est actif à la fois, choisi par ``TTS_PROVIDER`` et construit par
``jarvis.audio.tts.factory``. Aucun module en dehors de la fabrique ne doit
importer ces modules : c'est la règle qui garde le pipeline indépendant du
moteur.
"""
