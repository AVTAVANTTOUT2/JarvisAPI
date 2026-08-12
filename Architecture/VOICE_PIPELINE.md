# Pipeline vocal cognitif

Dernière mise à jour : 2026-08-12

## Rôle

Réponses vocales instantanées (Flash), avec délégation Cursor ou raisonnement Main en arrière-plan quand la tâche est lourde, sans bloquer le micro.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `api/voice_processing.py` | Adaptateur voix : fast-paths déterministes, latences, délégation au moteur canonique |
| `api/chat_processing.py` | Moteur canonique : contexte, LLM, confirmations, actions, suivi et réponse structurée |
| `api/voice_cognitive.py` | Briefings variants, ack Cursor/heavy, follow-up Main |
| `api/ws_handsfree.py` | Mode mains libres WebSocket |
| `api/mobile_voice_service.py` | Android → `_process_voice_fast` |
| `database/devops.py` | `get_voice_latency_metrics` (p50/p95) |
| `api/router_cognitive.py` | `GET /api/voice/metrics` |
| `jarvis/voice/address.py` | Politique d'adresse : types d'énoncés, budget de session, filtre déterministe |

## Flux type (mains libres / Android)

```
audio → STT local
  ├ `small` confiant → résultat immédiat
  └ confiance faible → signal `voice_quality_fallback` (silencieux) + relecture qualité
  → route_request(..., interaction_mode="voice")
  → maybe_handle_cognitive_voice()
       ├ briefing → BriefingEngine (voice_text)
       ├ cursor → ack Flash + enqueue job
       ├ heavy → ack + tâche Main async + résumé Flash + notif high
       └ sinon → `_process_message_internal(..., voice_mode=True)`
                    ├ signal d'état `voice_processing_started` (jamais audible)
                    ├ contexte et routage canoniques
                    ├ DeepSeek Flash court (`VOICE_MAX_TOKENS`)
                    └ action + action_result structurés
  → politique d'adresse → TTS → playback
```

### Confirmation Cursor (« lance »)

Après un ack de délégation, « lance » / « vas-y » démarre le job en attente via `api/voice_cognitive.py` (même priorité que le chat : une proposition shell/food/terminal liée à la session prime sur le job Cursor). Détails et phrases reconnues : [`Architecture/COGNITIVE_ROUTING.md`](COGNITIVE_ROUTING.md#délégation-cursor--proposition-et-confirmation).

### Barge-in

Phrases de contrôle dans `voice_processing` (stop / silence / annule) interrompent TTS et réouvrent l’écoute sans re-router une requête métier.

### Briefing vocal

`_detect_briefing_variant` mappe le texte vers :

- `morning` / `evening`
- `voice_only` (texte court uniquement)
- `work_only` / `urgent_only`
- `delta` (depuis le snapshot matin)

## Métriques

Table `voice_debug_log` : latences STT / routing / LLM / TTS / total.

La métrique de référence est `end_of_speech_to_first_audio_ms`. La trace
matérielle du 5 août 2026 mesure **1 758,3 ms** sur le pire chemin exercé
(`small` peu confiant, modèle qualité froid, Qwen3 Base et vraie écriture
CoreAudio), sous la cible de 2 000 ms. Voir
[`docs/audio/QWEN3_LOCAL_STATUS.md`](../docs/audio/QWEN3_LOCAL_STATUS.md).

`GET /api/voice/metrics` → agrégats (moyenne, p50, p95) sur fenêtre récente.

## Config

```bash
VOICE_MAX_TOKENS=500
VOICE_SILENCE_DURATION_MS=1200
VOICE_MIN_SPEECH_MS=400
TTS_PROVIDER=qwen3_local
STT_ENGINE=local
STT_MODEL=small
STT_FALLBACK_MODEL=large-v3-turbo
STT_QUALITY_FALLBACK_LOGPROB=-0.35
VOICE_ADDRESS_POLICY=rare
VOICE_PROGRESS_ACK_POLICY=long_jobs_only
VOICE_REASONING_MODEL=   # défaut = DeepSeek Flash
```

## Politique de parole

Un tour normal produit **exactement une** prise de parole : la réponse. Aucune
phrase générique ne la précède.

L'accusé anticipé « Bien, Monsieur. » a été supprimé. Il ajoutait un énoncé
devant presque chaque réponse, et le tour attendait sa **lecture** avant de
rendre la vraie réponse — en semi-duplex il fermait de surcroît le flux
d'entrée, si bien qu'une sortie anticipée laissait le micro clos. Ce qui le
remplace n'est pas une phrase plus courte mais un changement d'état :
`voice_processing_started`, purement visuel.

Un accusé **parlé** reste légitime lorsqu'un travail long a réellement été
accepté (« Je lance l'analyse. ») : la progression d'un job et le temps de
premier jeton d'un LLM sont deux notions distinctes, et seule la première
mérite d'être annoncée. Elle est réglée par `VOICE_PROGRESS_ACK_POLICY`.

### « Monsieur »

L'honorifique n'est pas supprimé, il est rationné — `VOICE_ADDRESS_POLICY`.

| Type d'énoncé | « Monsieur » |
|---|---|
| Réponse conversationnelle ou d'outil | interdit |
| Confirmation d'action, progression | interdit |
| Erreur, repli, réponse vide | interdit |
| Interpellation, barge-in | interdit |
| Ouverture réelle de session | une fois au maximum |
| Fermeture réelle de session | une fois au maximum |
| Rituel proactif | une fois au maximum |

Deux garanties, pas une seule : le prompt (`VOICE_ADDRESS_OVERLAY`, `persona.txt`)
et un filtre déterministe appliqué après génération. Le prompt suffit *la
plupart du temps* ; « la plupart du temps » ne convient pas pour un mot que
l'utilisateur entend à chaque tour, et il ne touche de toute façon pas les
producteurs qui ne sont pas des modèles (fast-paths, replis d'action, cache TTS).

Le filtre ne fait jamais de remplacement global. Il laisse intacts les
citations, les titres d'œuvre, la civilité d'un tiers (« Monsieur Dupont »),
l'emploi comme nom commun et le mot en position de sujet. Une frontière de
session n'est pas une détection de wake word : réveiller JARVIS trois fois pour
trois questions reste la même conversation.

## Android

`api/mobile_voice_service.py`, `api/ws_handsfree.py` et le daemon appellent le
même adaptateur `_process_voice_fast`. Celui-ci délègue tout tour non
déterministe à `_process_message_internal` : il n'existe plus de prompts,
budgets, parsing ou exécution d'action propres à la voix. Les transports
publient `action` et `action_result` séparément du texte prononcé.

## Limites connues

- Latences p50/p95 nécessitent un volume de tours réels en `voice_debug_log`.
- Follow-up heavy Main est asynchrone : l’utilisateur reçoit d’abord l’ack, puis une notif / résumé.
- Le pipeline reste séquentiel après la fin de parole : STT complet, puis LLM,
  puis TTS. Le STT incrémental, l'endpointing adaptatif, l'annulation d'écho et
  le barge-in plein duplex ne sont **pas** couverts par ce lot — voir
  `Architecture/adr/ADR-028-politique-de-parole-vocale.md`, section « Ce que ce
  lot ne fait pas ».
- Pas de bascule Ollama pour la voix.
