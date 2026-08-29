# Audit d’intégration — JARVIS Voice HUD

**Date :** 29 août 2026
**Base auditée :** `main` @ `d42ccab`

## Architecture trouvée

- Le backend canonique est l’application FastAPI assemblée dans `main.py`.
- Le canal conversationnel est `/ws`, pas `/ws/voice`. Il multiplexe texte,
  audio et conversation mains libres dans `api/ws_handler.py`.
- Tous les transports vocaux convergent vers `_process_voice_fast`, puis vers
  `_process_message_internal`. Le résultat structuré existant contient le texte
  prononcé, l’action, son résultat et un snapshot de provenance `knowledge`.
- Le daemon natif publie déjà ses états via le callback injecté dans
  `api/lifespan.py`. Le TTS local et sa politique de parole restent inchangés.
- Le frontend canonique est `frontend/` (Next.js 15). Il réutilise les vues de
  `web/`; `frontend/out` est un artefact généré et n’est pas modifié à la main.
- L’authentification navigateur existante protège les routes `/api/*`; les
  WebSockets par cookie exigent une origine exacte.
- Le canal TV fournit le précédent utile : fan-out descendant, files bornées,
  échec d’un consommateur sans impact sur le producteur.

## Contrats réutilisés

- réponse canonique de `_process_message_internal` ;
- références réelles et expurgées de `public_knowledge_payload` ;
- résultats structurés de `execute_action` ;
- identités de conversation et de tour existantes ;
- annulation TTS et barge-in du mode mains libres ;
- `LockGate`, cookie de session et validation d’origine WebSocket ;
- build statique et routeur du frontend unifié.

## Écarts avec les hypothèses initiales

- Il n’existait ni `/ws/voice` séparé, ni transcription STT partielle. Le STT
  local produit une transcription finale après la fin de l’énoncé. Le contrat
  HUD accepte les événements partiels sans en fabriquer lorsque le producteur
  ne les fournit pas.
- La provenance conversationnelle est parfois un identifiant opaque volontaire
  (`source_type`, `source_id`). Les titres et extraits ne sont disponibles que
  lorsque l’outil les retourne réellement.
- Le TTS navigateur produit un blob complet. La synchronisation fiable est donc
  au niveau réponse/segment sémantique, pas mot par mot.

## Décision

Ajouter un coordinateur en mémoire limité aux sessions vocales, consommé par
`GET /api/voice-display/snapshot` et `WS /ws/voice-display`. Les producteurs
existants publient sans attente et continuent normalement lorsque le feature
flag est coupé ou qu’aucun écran n’est connecté. La route `/voice-display` vit
dans le frontend unifié et contourne uniquement le layout applicatif, jamais
le verrou d’authentification.

## Risques et stratégie de test

- **Régression vocale :** appels HUD fail-open et tests vocaux existants.
- **Fausse provenance :** sources construites exclusivement depuis `knowledge`
  et `action_result`, avec invariant « confirmé ⇒ source ».
- **Reconnexion/duplication :** séquence monotone, snapshot, replay borné et
  reducer qui ignore doublons et retardataires.
- **24/7 :** files, activités, pile de navigation et historique frontend bornés ;
  test de 5 000 événements.
- **Sécurité :** flux descendant, trames mutantes fermées en `4405`, redaction,
  mode privé et expiration locale.
