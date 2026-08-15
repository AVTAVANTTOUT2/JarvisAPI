"""Recettes de raccourcis Apple packagées pour brancher iPhone/Mac sur JARVIS.

Les fichiers ``.shortcut`` binaires signés ne sont pas versionnés ici : Apple
exige une signature locale. Chaque recette décrit les actions à assembler dans
l'app Raccourcis, avec les URLs et en-têtes exacts attendus par le backend.

Frontière ADR-016 / ADR-029 :
- Mail, Calendar, Messages, Contacts → AppleScript (données).
- Raccourcis personnalisés (HomeKit, Siri, Back Tap, Focus) → CLI ``shortcuts``.
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
        "triggers": ["Automatisation Temps", "App Ouverture (optionnel)"],
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
        "triggers": ["Siri", "Back Tap", "Centre de contrôle", "Widget"],
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
        "triggers": ["Siri", "Back Tap", "Action Button", "Apple Watch"],
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
            "Créer le raccourci dans l'app Raccourcis (ex. HomeKit / Contrôle maison)",
            "Ouvrir /shortcuts dans JARVIS → onglet Installés",
            "Cliquer « Enregistrer » sur le nom exact (ou POST registry)",
            "Optionnel : définir un alias (« chambre ») et un risque",
            'Dire à JARVIS : « lance le raccourci chambre »',
            "Confirmer le plan affiché dans le chat ou à la voix",
        ],
        "triggers": ["Voix JARVIS", "Chat", "POST /prepare + /confirm"],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/registry"},
        "auth": "session cookie + CSRF",
    },
    {
        "id": "siri_phrase_homekit",
        "title": "Siri — phrase personnalisée vers un raccourci maison",
        "platform": "ios_macos",
        "summary": (
            "Ajoute une phrase Siri sur un raccourci HomeKit déjà dans le "
            "registre JARVIS, pour le déclencher hors JARVIS aussi."
        ),
        "requires": ["Raccourci HomeKit créé", "APPLE_SHORTCUTS_ENABLED=true"],
        "steps": [
            "Dans Raccourcis.app → raccourci → (i) → Ajouter à Siri",
            "Choisir une phrase claire (« allume la chambre »)",
            "Enregistrer le même nom exact dans /shortcuts (registre JARVIS)",
            "Depuis JARVIS : voix ou chat → confirmation obligatoire",
            "Depuis Siri : exécution native Apple, hors plan JARVIS",
        ],
        "triggers": ["Siri", "Voix JARVIS"],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/prepare"},
        "auth": "session cookie + CSRF (côté JARVIS)",
    },
    {
        "id": "focus_mode_bridge",
        "title": "Mode Concentration → notifier JARVIS",
        "platform": "ios",
        "summary": (
            "Quand un mode Concentration s'active, envoie un signal texte à "
            "JARVIS (ask) pour journaliser le contexte."
        ),
        "requires": ["APPLE_SHORTCUTS_INGEST_TOKEN"],
        "steps": [
            "Automatisation → Mode Concentration → Quand « Travail » s'active",
            "POST /api/apple/shortcuts/ask avec "
            '{"text":"Mode Travail activé","source":"focus"}',
            "Authorization: Bearer <APPLE_SHORTCUTS_INGEST_TOKEN>",
            "Exécuter sans demander",
        ],
        "triggers": ["Automatisation Concentration"],
        "endpoint": {"method": "POST", "path": "/api/apple/shortcuts/ask"},
        "auth": "Bearer APPLE_SHORTCUTS_INGEST_TOKEN",
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
