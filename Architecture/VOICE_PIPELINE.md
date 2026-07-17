# Pipeline vocal cognitif

Dernière mise à jour : 2026-07-16

## Rôle

Réponses vocales instantanées (Flash), avec délégation Cursor ou raisonnement Main en arrière-plan quand la tâche est lourde, sans bloquer le micro.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `api/voice_processing.py` | STT → traitement → TTS ; barge-in ; latences |
| `api/voice_cognitive.py` | Briefings variants, ack Cursor/heavy, follow-up Main |
| `api/ws_handsfree.py` | Mode mains libres WebSocket |
| `api/mobile_voice_service.py` | Android → `_process_voice_fast` |
| `database/devops.py` | `get_voice_latency_metrics` (p50/p95) |
| `api/router_cognitive.py` | `GET /api/voice/metrics` |

## Flux type (mains libres / Android)

```
audio → STT local
  → route_request(..., interaction_mode="voice")
  → maybe_handle_cognitive_voice()
       ├ briefing → BriefingEngine (voice_text)
       ├ cursor → ack Flash + enqueue job
       ├ heavy → ack + tâche Main async + résumé Flash + notif high
       └ sinon → pipeline Flash court (VOICE_MAX_TOKENS)
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

`GET /api/voice/metrics` → agrégats (moyenne, p50, p95) sur fenêtre récente.

## Config

```bash
VOICE_MAX_TOKENS=500
VOICE_SILENCE_DURATION_MS=1200
VOICE_MIN_SPEECH_MS=400
TTS_ENGINE=edge   # ou ttskit | kokoro | macos
AUDIO_DAEMON_STT_ENGINE=local
VOICE_REASONING_MODEL=   # défaut = DeepSeek Flash
```

## Android

`api/mobile_voice_service.py` appelle le même chemin `_process_voice_fast` que le web — pas de pipeline parallèle « lite » sans routeur.

## Limites connues

- Latences p50/p95 nécessitent un volume de tours réels en `voice_debug_log`.
- Follow-up heavy Main est asynchrone : l’utilisateur reçoit d’abord l’ack, puis une notif / résumé.
- Pas de bascule Ollama pour la voix.
