# 07 — Feuille de route active

**Revue :** 27 août 2026
**Référence code :** `origin/main` @ `6becf26cb3ea4ab47acb1996a2a9125500446ab7`
**Source des statuts :** [registre de vérité](./project_truth_registry.json) et
[vue générée](./28_VALIDATION_COHERENCE.md).

Ce document fixe l’ordre de livraison et les preuves de sortie. Il ne redéfinit
pas l’état du code : toute évolution d’un statut se fait d’abord dans le
registre, avec ses preuves, puis cette vue est révisée. Les anciens numéros de
PR et métriques de juillet sont conservés uniquement dans les documents classés
`historical`.

## Légende

| État roadmap | Statut du registre |
|---|---|
| fait | `IMPLEMENTED_VERIFIED` |
| partiel | `PARTIAL` |
| matériel requis | `IMPLEMENTED_NEEDS_REAL_VALIDATION` |
| à faire | écart explicite d’une entrée `PARTIAL` ou `NOT_IMPLEMENTED` |
| futur | capacité volontairement absente et non engagée pour la release courante |

## Lots actifs

| Priorité | Famille / entrée du registre | État | Propriétaire ou environnement | Preuve actuelle | Preuve de fin attendue | Dépendances |
|---|---|---|---|---|---|---|
| P0 | Sécurité — `security` | partiel | backend + CI + revue sécurité | middleware auth/CSRF et audit de confidentialité versionné | PR de sécurité séparée intégrée par SHA, scan PII/secrets sans écart, frontières egress testées, aucune donnée réelle dans les artefacts publics | PR #282 traitée séparément ; politique de release |
| P0 | Release — `release` | partiel | CI puis Mac/appareils cibles | builds et checklists versionnés | candidat signé, notes, hashes, rollback testé et artefacts archivés | sorties sécurité, Android et macOS |
| P1 | Pilotage agentique — `task-control` | partiel | backend + clients canonique/macOS/Android | persistance, approbation de plan et garde fail-closed automatisés | vraie tâche longue : plan lu/approuvé, états task/run cohérents, rapport visible dans chaque client, reprise après crash sans double effet | provider réel et validation humaine |
| P1 | OpenCode — `opencode` | matériel requis | binaire et fournisseur réels dans un worktree jetable | adaptateur et pont MCP testés hors processus réel | tâche de développement réelle vérifiée, annulation/reprise, démontabilité du provider et worktree propre | pilotage agentique |
| P1 | Enregistrements longs — `long-recordings` | partiel | backend + navigateur canonique | spool persistant, réconciliation et purge testés | scénarios virtuels 1/30/180 min, reprise, dédoublonnage, annulation, progression/export UI et RAM bornée prouvés | protocole client canonique |
| P1 | Audio — `audio` | matériel requis | Mac cible, micro et haut-parleur réels | STT local, Qwen3-TTS local et daemon testés hors matériel | permissions micro, coupure/reconnexion, files bornées, campagne 24 h et écoute humaine archivées | enregistrements longs pour les scénarios prolongés |
| P1 | Android — `android` | matériel requis | téléphone physique cible | app native, tests JVM/instrumentés et garde-fous release versionnés | pairage, Keystore, biométrie, Room, WorkManager, DataStore, chat/WS/offline, GPS, FCM, wake word, voix, navigation et diagnostics rejoués sur appareil | backend HTTPS et candidat release |
| P1 | macOS — `macos` | matériel requis | Mac cible + identité Developer ID | app SwiftUI, widget, cible de tests et build Release CI | signature, notarisation, réseau/Tailscale, terminal SSH, widget, notifications, task-control, veille et reconnexion validés | candidat release |
| P2 | Observabilité — `observability` | partiel | backend + exploitation | sondes et historique de métriques testés | export, alertes et SLO opérables pendant la campagne 24 h | pile stable |
| P2 | Fonctionnalités Android futures | futur | produit Android | absence déclarée dans [FUTURE_FEATURES](../android/docs/FUTURE_FEATURES.md) | une PR par capacité avec code, tests et validation appareil ; sinon le placeholder reste inerte | release courante non bloquée |

## Backlog Android explicitement futur

Les capacités suivantes restent `futur` parce que leur logique est déclarée
absente et leurs placeholders sont inertes : conversation vocale continue,
wake word avancé, carte live, historique de trajets, création d’événement,
mutations de tâches, pièces jointes chat, slash commands, actions de
notifications, multi-device, détail de file offline, vues mémoire et contacts,
automatisations, widgets et dashboard personnalisable. La preuve détaillée et
les points de branchement vivent dans
[`android/docs/FUTURE_FEATURES.md`](../android/docs/FUTURE_FEATURES.md).

## Ordre recommandé

1. Fermer la confidentialité publique et la PR de sécurité séparée ; ne publier
   aucun artefact tant que ces portes ne sont pas vertes.
2. Livrer le vertical client des enregistrements longs et ses scénarios
   déterministes 1/30/180 min.
3. Valider une tâche agentique réelle avec OpenCode, y compris reprise,
   annulation, rapport client et retrait du provider.
4. Rejouer les matrices matérielles audio, Android et macOS.
5. Exécuter la campagne 24 h, compléter observabilité/SLO, puis produire le
   candidat signé.
6. Publier seulement après notes de version, hashes, preuve de rollback et
   approbation humaine.

## Critères de sortie globaux

- aucune entrée P0 avec écart ouvert ;
- chaque état `fait` possède une preuve code et une preuve automatisée qui
  démontrent précisément l’affirmation ;
- chaque état `matériel requis` possède un artefact daté du matériel cible ;
- aucun secret, chemin utilisateur, IP personnelle, numéro de série, capture
  issue de données réelles ou donnée vocale réelle dans les fichiers publics ;
- liens Markdown, générateurs, registre de dette, OpenAPI/SDK concernés et suite
  pertinente verts ;
- aucune campagne, signature, notarisation ou validation physique déclarée sans
  artefact ;
- rollback documenté et testable avant toute release.
