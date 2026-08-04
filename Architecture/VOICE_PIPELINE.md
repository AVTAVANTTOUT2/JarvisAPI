# Pipeline vocal cognitif

Dernière mise à jour : 2026-08-04

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

## Flux type (mains libres / Android)

```
audio → STT local
  ├ `small` confiant → résultat immédiat
  └ confiance faible → accusé Qwen3 local + relecture qualité en parallèle
  → route_request(..., interaction_mode="voice")
  → maybe_handle_cognitive_voice()
       ├ briefing → BriefingEngine (voice_text)
       ├ cursor → ack Flash + enqueue job
       ├ heavy → ack + tâche Main async + résumé Flash + notif high
       └ sinon → `_process_message_internal(..., voice_mode=True)`
                    ├ accusé local concurrent si le STT ne l'a pas déjà lancé
                    ├ contexte et routage canoniques
                    ├ DeepSeek Flash court (`VOICE_MAX_TOKENS`)
                    └ action + action_result structurés
  → TTS → playback
```

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
matérielle du 4 août 2026 mesure **1 483,4 ms** sur le pire chemin exercé
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
STT_QUALITY_FALLBACK_LOGPROB=-0.3
VOICE_ANTICIPATORY_ACK_ENABLED=true
VOICE_REASONING_MODEL=   # défaut = DeepSeek Flash
```

## Android

`api/mobile_voice_service.py`, `api/ws_handsfree.py` et le daemon appellent le
même adaptateur `_process_voice_fast`. Celui-ci délègue tout tour non
déterministe à `_process_message_internal` : il n'existe plus de prompts,
budgets, parsing ou exécution d'action propres à la voix. Les transports
publient `action` et `action_result` séparément du texte prononcé.

## Limites connues

- Latences p50/p95 nécessitent un volume de tours réels en `voice_debug_log`.
- Follow-up heavy Main est asynchrone : l’utilisateur reçoit d’abord l’ack, puis une notif / résumé.
- Pas de bascule Ollama pour la voix.
