"""Pile audio applicative de JARVIS — synthèse vocale locale.

Ce paquet ne contient que du code qui tourne sur la machine. Importer
``jarvis.audio`` ne charge aucun moteur, aucun modèle et n'ouvre aucune
connexion : la résolution du fournisseur passe par
``jarvis.audio.tts.create_local_tts_provider``.
"""
