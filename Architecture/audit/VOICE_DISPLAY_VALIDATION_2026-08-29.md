# Validation — JARVIS Voice HUD

**Date :** 29 août 2026
**Plateforme :** macOS, Python 3.12.13, pnpm 11.11.0
**Périmètre :** pipeline voix canonique, canal descendant Voice HUD, route kiosk,
provenance, navigation vocale, interruption/reprise, reconnexion et confidentialité.

## Verdict

Le Voice HUD est livrable derrière `VOICE_DISPLAY_ENABLED=false` par défaut. Il
ne crée ni moteur vocal ni boucle agentique parallèle : les événements sont
émis depuis le tour voix existant et transportent le résultat canonique déjà
utilisé par le TTS. Le canal `/ws/voice-display` est authentifié, descendant et
borné ; toute commande cliente autre que `pong` ferme la connexion en `4405`.

## Preuves automatisées

| Porte | Résultat |
|---|---|
| Suite Python complète | **3 350 passés**, 7 désélectionnés, 1 avertissement externe, 437,51 s |
| Tests voix/HUD ciblés | **110 passés** |
| Régression confirmation/journal/HUD | **59 passés** |
| Ruff complet | **OK** |
| Audit dette technique | **OK**, 41 dettes recensées, 0 active |
| Vérité d’architecture | **OK** |
| Contrat OpenAPI versionné | **synchronisé** |
| SDK Python généré | **24 tests passés**, wheel construite |
| Tests unitaires `frontend/` | **58 passés** |
| Tests unitaires `web/` | **83 passés** |
| TypeScript `frontend/` et `web/` | **OK** |
| Build Next.js de production | **OK**, export statique 33 pages |
| Playwright complet | **10 passés**, 5 workers |

La suite Python a été exécutée dans un worktree propre construit depuis les
commits Voice HUD, avec l’environnement macOS `.venv/`. Cette isolation était
nécessaire pour préserver des modifications ingestion/retrieval concurrentes du
worktree principal. Le venv CI léger `venv/` a aussi validé 3 333 tests, mais ne
contient volontairement ni `sqlcipher3` ni la version PyMuPDF 1.28 exigée par
les tests macOS complets.

## Charge et latence

Mesures locales, non assimilables à un benchmark matériel certifié :

- 20 000 événements partiels : publication p50 **9,8 µs**, p95 **12,3 µs** ;
- rétention backend maintenue à 512 événements ;
- 1 000 abonnements/désabonnements : aucun abonné résiduel ;
- mémoire tracée après la charge : **1 398,3 KiB** courants, **1 400,8 KiB** au pic ;
- résultat de 30 candidats borné à 12 sources et 3 sections en **2,4 ms** ;
- E2E navigateur : rendu résultat **23,2 ms**, rendu déconnexion **13,3 ms** ;
- test de stabilité : 300 tours, reconnexions répétées et 5 000 événements,
  historiques et files toujours bornés.

## Sécurité, vérité et confidentialité

- modèles Pydantic stricts et `schema_version=1` ;
- séquences monotones, reprise par snapshot et rejet des doublons côté client ;
- claims `confirmed` invalides sans source réelle ;
- sources construites uniquement depuis `knowledge.references` et
  `action_result`, jamais depuis le texte du modèle ;
- secrets, Bearer, clés, champs sensibles et chemins absolus expurgés avant
  émission ;
- aucun `dangerouslySetInnerHTML`, aucune exécution de contenu distant ;
- mode privé vocal et masquage automatique après inactivité ;
- aucun input, bouton ou action pointeur sur la route kiosk.

## Captures vérifiées

Les sept captures 1920 × 1080 sous `docs/assets/voice-display/` sont générées
par l’E2E avec des fixtures explicitement identifiées comme telles. Elles
couvrent veille, écoute/transcription, recherche, résultat, lecture de source,
confidentialité et perte de connexion.

## Limites connues et honnêtes

- Le producteur STT actuel fournit surtout la transcription finale. Le contrat
  et l’interface acceptent un flux partiel réel, mais le HUD n’en invente pas.
- Le TTS navigateur ne remonte pas de position mot à mot. L’interruption est
  immédiate et « continue » relit le texte canonique mémorisé sans nouvel appel
  LLM ; la synchronisation visuelle reste au segment, pas au mot.
- La campagne simule des milliers d’événements et des centaines de tours ; un
  soak physique de 24 h avec micro, écran et veille macOS n’a pas été exécuté.
- Les intégrations Apple et les appels LLM réels n’ont pas été sollicités : les
  tests utilisent des doubles explicitement cantonnés au périmètre test.

Ces limites n’entraînent aucun fallback trompeur : en l’absence de signal réel,
l’état correspondant n’est simplement pas publié.
