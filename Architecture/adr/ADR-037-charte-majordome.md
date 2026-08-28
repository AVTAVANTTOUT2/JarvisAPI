# ADR-037 : Charte majordome et primitifs `launch`

**Date** : 2026-08-20
**Statut** : Accepté
**Amende** : [ADR-029](./ADR-029-apple-shortcuts-bridge.md) (la confirmation n'est plus systématique)

## Contexte

`open_app` ne savait lancer que `open -a AppName`. « Ouvre YouTube, la chaîne de Squeezie » ouvrait Safari et demandait à l'utilisateur de naviguer. Chaque nouvelle app devenait une feature et une permission.

## Décision

1. **Un primitif `launch`** (`open` validé) : URL, schéma allowlisté, fichier sous `$HOME`, nom d'app. `open_app` reste un alias. Pas d'intégration YouTube.
2. **Charte `JARVIS_TRUST_PROFILE`** (`restricted` / `standard` / `majordomo`) : classes `local.launch` / `local.shortcuts`, jamais send / money / shell / destructive / missions code.
3. **Shortcuts** : honorer `requires_confirmation` déjà en base. Autorun seulement si case décochée **et** `risk=low` **et** profil `standard`/`majordomo`.
4. **TCC** : doctor lecture seule (`integrations/macos_permissions.py`). Pas de `tccutil reset`. Préférer `open` à AppleScript pour ne pas re-prompt par app.
5. **`AGENTIC_REQUIRE_PLAN_APPROVAL` inchangé.**

## Conséquences

« Ouvre la chaîne de Squeezie » exécute `open https://www.youtube.com/@Squeezie` sans confirmation. Un raccourci high reste confirmé même décoché.
