# ADR-029 : Pont Apple Shortcuts (Raccourcis.app) allowlisté

**Date** : 2026-08-15
**Statut** : Accepté

## Contexte

ADR-016 a choisi AppleScript comme unique intégration **données** Apple
(Mail, Calendar, Messages, Contacts). Les raccourcis personnalisés de
l'utilisateur (HomeKit, automatisations maison, flux iOS) vivent dans
**Shortcuts.app**, exposés par le CLI officiel `/usr/bin/shortcuts`.

Sans pont dédié, JARVIS ne peut ni lancer un raccourci HomeKit au verbal,
ni offrir des recettes iOS fiables au-delà du POST GPS legacy.

## Décision

1. **CLI `shortcuts`**, pas AppleScript — dictionnaire plus stable, options
   `--input-path` / `--output-path` contrôlables.
2. **Opt-in** `APPLE_SHORTCUTS_ENABLED=false` par défaut.
3. **Registre SQLite** (`apple_shortcut_registry`) : le LLM ne peut cibler
   que des noms/alias enregistrés par l'utilisateur.
4. **Plan opaque + confirmation** (même famille que `terminal` / `food_order`) :
   un `confirmed:true` du modèle sans plan serveur est ignoré.
5. **Entrée texte** écrite uniquement dans `APPLE_SHORTCUTS_WORKSPACE`
   (jamais un chemin fourni par le modèle).
6. **Ingest iOS** (`/api/apple/shortcuts/ask|task`) authentifié par
   `APPLE_SHORTCUTS_INGEST_TOKEN`, hors cookie de session, avec rate-limit.
7. **Recettes** documentées en JSON (`integrations/apple_shortcuts_recipes.py`)
   — pas de binaires `.shortcut` signés versionnés.

## Conséquences

- JARVIS peut dire « allume la chambre » si le raccourci est enregistré.
- Un raccourci inventé par le modèle est refusé avant toute exécution.
- Les recettes iOS (position, demande, tâche) sont assemblables à la main
  dans l'app Raccourcis en suivant `GET /api/apple/shortcuts/recipes`.
- Ne remplace pas AppleScript pour Mail/Calendar (ADR-016 inchangé).
