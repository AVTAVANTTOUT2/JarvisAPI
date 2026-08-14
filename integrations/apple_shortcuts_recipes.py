"""Recettes de raccourcis Apple packagées pour brancher iPhone/Mac sur JARVIS.

Les fichiers ``.shortcut`` binaires signés ne sont pas versionnés ici : Apple
exige une signature locale. Chaque recette décrit les actions à assembler dans
l'app Raccourcis, avec les URLs et en-têtes exacts attendus par le backend.
"""

from __future__ import annotations

from typing import Any

RECIPES: tuple[dict[str, Any], ...] = (
    {
        "id": "jarvis_location",
        "title": "JARVIS — Position",
        "platform": "ios",
        "summary": (
            "Envoie la position GPS courante à POST /api/location "
            "toutes les N minutes via une Automatisation."
        ),
        "requires": ["LOCATION_API_TOKEN", "URL du Mac (Tailscale ou LAN)"],
        "steps": [
            "Obtenir la position actuelle (précision : Naviguer)",
            "Obtenir le contenu de l'URL : méthode POST",
            "URL : https://<hote-jarvis>:8081/api/location",
            "En-têtes : Authorization = Bearer <LOCATION_API_TOKEN>, "
            "Content-Type = application/json",
            (
                "Corps JSON : "
                '{"latitude": Position.latitude, "longitude": Position.longitude, '
                '"accuracy": Position.précision, "altitude": Position.altitude, '
                '"source": "shortcut"}'
            ),
            "Automatisation : intervalle 5 minutes, exécuter sans demander",
        ],
        "endpoint": {"method": "POST", "path": "/api/location"},
        "auth": "Bearer LOCATION_API_TOKEN",
    },
    {
        "id": "jarvis_ask",
        "title": "JARVIS — Demander",
        "platform": "ios_macos",
        "summary": (
            "Pose une question à JARVIS depuis Raccourcis et affiche la réponse. "
            "Authentifié par APPLE_SHORTCUTS_INGEST_TOKEN."
        ),
        "requires": ["APPLE_SHORTCUTS_INGEST_TOKEN", "APPLE_SHORTCUTS_ENABLED=true"],
        "steps": [
            "Demander : « Que dois-je dire à JARVIS ? » → Magasin Texte",
            "Obtenir le contenu de l'URL : méthode POST",
            "URL : https://<hote-jarvis>:8081/api/apple/shortcuts/ask",
            "En-têtes : Authorization = Bearer <APPLE_SHORTCUTS_INGEST_TOKEN>, "
            "Content-Type = application/json",
            'Corps JSON : {"text": Magasin Texte, "source": "shortcut"}',
            "Afficher réponse.reply (Notification ou Dialogue)",
        ],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/ask"},
        "auth": "Bearer APPLE_SHORTCUTS_INGEST_TOKEN",
    },
    {
        "id": "jarvis_quick_task",
        "title": "JARVIS — Tâche rapide",
        "platform": "ios_macos",
        "summary": (
            "Crée une tâche JARVIS sans conversation, depuis un raccourci "
            "personnalisé (Back Tap, Control Center, Siri)."
        ),
        "requires": ["APPLE_SHORTCUTS_INGEST_TOKEN"],
        "steps": [
            "Demander : « Titre de la tâche »",
            "POST https://<hote-jarvis>:8081/api/apple/shortcuts/task",
            "Authorization: Bearer <APPLE_SHORTCUTS_INGEST_TOKEN>",
            'Corps : {"title": Texte, "priority": "medium", "source": "shortcut"}',
            "Afficher confirmation",
        ],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/task"},
        "auth": "Bearer APPLE_SHORTCUTS_INGEST_TOKEN",
    },
    {
        "id": "register_homekit_example",
        "title": "Exemple — enregistrer un raccourci HomeKit dans JARVIS",
        "platform": "macos",
        "summary": (
            "Après avoir créé « allume la chambre » dans Raccourcis, "
            "enregistre-le dans le registre JARVIS pour que la voix puisse "
            "le lancer avec confirmation."
        ),
        "requires": ["APPLE_SHORTCUTS_ENABLED=true", "session web JARVIS"],
        "steps": [
            "Créer le raccourci dans l'app Raccourcis (ex. HomeKit)",
            "GET /api/apple/shortcuts/installed pour vérifier le nom exact",
            (
                "POST /api/apple/shortcuts/registry "
                '{"name":"allume la chambre","alias":"chambre","risk":"medium"}'
            ),
            'Dire à JARVIS : « lance le raccourci chambre »',
            "Confirmer le plan affiché",
        ],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/registry"},
        "auth": "session cookie + CSRF",
    },
)


def list_recipes() -> list[dict[str, Any]]:
    return [dict(recipe) for recipe in RECIPES]


def get_recipe(recipe_id: str) -> dict[str, Any] | None:
    needle = (recipe_id or "").strip().lower()
    for recipe in RECIPES:
        if recipe["id"] == needle:
            return dict(recipe)
    return None
