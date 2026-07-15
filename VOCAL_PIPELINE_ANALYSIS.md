# Analyse du pipeline vocal JARVIS — instantané historique
_Généré le 29 juin 2026 à 23:28 — analyse statique du code_

> Ce document décrit le pipeline antérieur à la PR #17. Il explique les
> décisions passées mais n'est plus la source de vérité. Voir
> `Architecture/30_PLAN_STABILISATION_AUDIO.md` pour l'état actuel.

## Diagramme de flux

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PIPELINE VOCAL JARVIS                             │
│              3 boucles asyncio + 1 watchdog                          │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────┐
│ ÉTAPE 1 — CAPTURE MICRO      │  audio_daemon.py:522-602
│                               │
│  [Thread PyAudio               │
│   Blue Snowball, 16kHz mono    │
│   stream.read(CHUNK_SAMPLES)   │
│   → _chunk_rms() vérif muet    │
│   → loop.call_soon_threadsafe  │
│   → _safe_put(data)           │]──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 2 — QUEUE AUDIO        │  audio_daemon.py:168,280
│                               │
│  asyncio.Queue[bytes]         │
│  maxsize=300                  │
│  300 × 960 bytes = 288 KB     │
│  = ~9s de buffer audio        │
│  (drain si >100 frames)       │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 3 — VAD (_vad_loop)    │  audio_daemon.py:506-814
│                               │
│  Mode listening : consomme    │
│  queue.get() timeout 0.1s     │
│  RMS > 0.02 = parole          │
│  Silence ≥ 1.5s = fin phrase  │
│  Max 15s = flush forcé        │
│  b"".join(frames) → utterance │
│  → utterance_queue.put_nowait │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 4 — PROCESS LOOP       │  audio_daemon.py:818-891
│                               │
│  Attend utterance_queue       │
│  → _process_single_utterance │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 5 — PCM → WAV          │  audio_daemon.py:1367-1375
│                               │
│  _pcm_to_wav(pcm_bytes)       │
│  io.BytesIO + wave.writeframes│
│  → wav_bytes                  │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 6 — STT                │  audio_daemon.py:912-949
│                               │
│  Priorité 1: ancien fournisseur audio cloud ancien STT cloud│  audio/stt.py:31-84
│    POST /v1/speech-to-text    │
│    legacy_stt_v2, audio/webm MIME │
│    ~300ms latence cloud        │
│  Priorité 2: faster-whisper   │  audio/stt_local.py:60+
│    local, tiny 75 Mo           │
│    ~50ms latence               │
│  → str (transcription)        │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 7 — FILTRES POST-STT   │  audio_daemon.py:959-979
│                               │
│  Skip si < 3 chars            │
│  Skip si résidu écho          │
│    (< 10 chars & < 2s TTS)    │
│  Skip si phrase de fin        │
│    ("merci jarvis"...)        │
│  Broadcast transcript         │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 8 — LLM                │  main.py:3630-3842
│                               │
│  _process_voice_fast()        │
│  ┌──────────────────────────┐│
│  │ Pass 1: DeepSeek flash    ││
│  │  - _get_horodatage()      ││
│  │  - historique 10 msg      ││
│  │  - system prompt custom   ││
│  │  - max_tokens=300         ││
│  │  - temperature=0.7        ││
│  │  → reponse OU bloc action ││
│  │                           ││
│  │ Si bloc action trouvé :   ││
│  │  → execute_action()       ││
│  │  │   actions.py:24        ││
│  │  │   17 types action       ││
│  │  │                        ││
│  │  → Pass 2: reformulation  ││
│  │  - max_tokens=200         ││
│  │  - fallback_action_resp() ││
│  │  si LLM échoue            ││
│  └──────────────────────────┘│
│  → {text, emotion, cost}     │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 9 — TTS                │  audio_daemon.py:1037-1048
│                               │
│  _play_tts(text, emotion)     │  audio_daemon.py:1266-1309
│  ┌──────────────────────────┐│
│  │ Priorité 1: Edge TTS      ││  audio/tts.py:156-177
│  │  edge_tts.Communicate()   ││
│  │  voix: fr-FR-HenriNeural   ││
│  │  → fichier MP3 temporaire ││
│  │  ~200ms                    ││
│  │                           ││
│  │ Priorité 2: macOS natif   ││
│  │  say + afconvert → M4A   ││
│  │  zéro réseau               ││
│  │                           ││
│  │ Priorité 3: say direct    ││
│  │  subprocess "say"         ││
│  └──────────────────────────┘│
│  → bytes audio               │──┐
└──────────────────────────────┘   │
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 10 — PLAYBACK          │  audio_daemon.py:1311-1340
│                               │
│  _play_audio_local(bytes)     │
│  Détection format :           │
│    RIFF → .wav                │
│    ID3/0xFF → .mp3            │
│    sinon → .m4a               │
│  NamedTemporaryFile → afplay  │
│  subprocess async wait()      │
│  unlink fichier temp          │
└──────────────────────────────┘
                                   ▼
┌──────────────────────────────┐
│ ÉTAPE 11 — PURGE + REPRISE   │  audio_daemon.py:1052-1086
│                               │
│  sleep(0.5) anti-écho         │
│  Drain _audio_queue           │
│  Drain _utterance_queue       │
│  stream.start_stream()        │
│  state → "listening"          │
│  _tts_playing = False         │
└──────────────────────────────┘
```

---

## Étape 1 — Capture micro

- **Fichier** : `scripts/audio_daemon.py` lignes 37-42, 522-602
- **Format audio** : PCM 16-bit signed, 16 kHz, mono
  ```python
  SAMPLE_RATE = 16000        # ligne 37
  CHANNELS = 1               # ligne 38
  SAMPLE_WIDTH = 2           # ligne 39 (16-bit)
  CHUNK_MS = 30              # ligne 40
  CHUNK_SAMPLES = 480        # ligne 41 (= 16000 × 30 / 1000)
  CHUNK_BYTES = 960          # ligne 42 (= 480 × 2)
  ```
- **Périphérique** : Blue Snowball, résolu par `_resolve_input_device_index()` (ligne 1377-1410). Priorité : `AUDIO_DAEMON_INPUT_DEVICE` dans `.env` → auto-détection "Blue Snowball" → défaut système.
- **Thread pyaudio** : fonction `_blocking_input()` (ligne 522-600), lancée via `loop.run_in_executor(None, _blocking_input)` (ligne 602).
- **Ouverture stream** :
  ```python
  stream = pa.open(
      rate=SAMPLE_RATE,           # 16000
      channels=CHANNELS,          # 1
      format=8,                   # pyaudio.paInt16
      input=True,
      input_device_index=self._resolve_input_device_index(pa),
      frames_per_buffer=CHUNK_SAMPLES,  # 480
  )
  ```
  (lignes 527-534)
- **Détection micro muet** (permission macOS refusée) : lignes 539-556. Si RMS ≤ 0.0001 pendant 3s continues, log CRITICAL.

### Thread-safety (comment les frames arrivent dans la coroutine async)

- **Méthode** : `loop.call_soon_threadsafe(_safe_put, data)` (ligne 585)
- La fonction `_safe_put(data_bytes)` (lignes 565-583) est exécutée par la boucle d'événement asyncio depuis le thread pyaudio :
  - `audio_queue.put_nowait(data_bytes)` — insertion non-bloquante
  - Si `QueueFull` : drain intelligent — supprime les frames les plus anciennes si `qsize() > 100`, puis réessaie
- **Anti-écho pendant TTS** : si `self._tts_playing` est True, les frames lues du micro sont jetées (ligne 561-562) — jamais insérées dans la queue.

---

## Étape 2 — Queue audio

- **Fichier** : `scripts/audio_daemon.py` ligne 168, recréée ligne 280
- **Type** : `asyncio.Queue[bytes](maxsize=300)`
- **Taille max** : 300 frames × 960 bytes = **288 KB** = ~**9 secondes** d'audio bufferisé
- **Drain** : si la queue est pleine, `_safe_put` drain à `qsize()` 100 frames restantes avant d'insérer (lignes 568-583)

---

## Étape 3 — VAD (Voice Activity Detection)

- **Fichier** : `scripts/audio_daemon.py` lignes 506-814
- **Fonction** : `_vad_loop_safe()` (ligne 506)
- **Paramètres** (lus depuis `.env` via `config.py`) :
  | Paramètre | Config | Valeur actuelle | Ligne |
  |-----------|--------|----------------|-------|
  | Seuil RMS parole | `AUDIO_DAEMON_SPEECH_THRESHOLD` | **0.02** | 606 |
  | Silence fin phrase | `AUDIO_DAEMON_SILENCE_MS` | **1500 ms** (= 50 chunks) | 605, 611 |
  | Durée min parole | `AUDIO_DAEMON_MIN_SPEECH_MS` | **600 ms** (= 20 chunks) | 607, 612 |
  | Max utterance | `AUDIO_DAEMON_MAX_UTTERANCE_S` | **15 s** (= 500 chunks) | 608, 613 |
  | Timeout conversation | `AUDIO_DAEMON_CONVERSATION_TIMEOUT` | **45.0 s** | 609 |
- **Seuil de silence adaptatif** : si la parole dure moins de 2s, le silence exigé passe à 2s minimum (lignes 614-615, 754-756) :
  ```python
  ADAPTIVE_SILENCE_CHUNKS = int(2000 / CHUNK_MS)  # = 66 chunks
  effective_silence = max(silence_chunks, ADAPTIVE_SILENCE_CHUNKS) if speech < 66
  ```
- **Pré-buffer** : 10 chunks (300 ms) avant le seuil de parole, injectés si parole confirmée (lignes 616, 735-737, 745-746)
- **Détection** :
  1. Calcule `_chunk_rms(chunk)` (ligne 1357-1364) : `sqrt(Σ s² / n) / 32768.0`
  2. Si RMS > 0.02 → `has_speech=True`, compteur `speech_chunks++`
  3. Si `has_speech` et RMS ≤ 0.02 → `silent_chunks++`
  4. Fin de phrase si `silent_chunks ≥ effective_silence` ET `speech_chunks ≥ min_speech_chunks` (ligne 762)
  5. Flush forcé si `total_chunks ≥ max_chunks` (= 500, soit 15s) (ligne 759)
- **Sortie** : `b"".join(frames)` → `utterance_queue.put_nowait(audio_bytes)` (lignes 774-785)
- **Timeout conversation** : après 45s sans interaction, retour à l'état d'écoute (lignes 787-802)
- **Protection erreurs** : après 50 erreurs consécutives → `RuntimeError` → restart du `_run()` (lignes 807-811)

---

## Étape 4 — Process loop

- **Fichier** : `scripts/audio_daemon.py` lignes 818-891
- **Fonction** : `_process_loop_safe()` (ligne 818)
- Attend l'`utterance_queue` avec timeout 2.0s (ligne 852)
- Appelle `_process_single_utterance(audio_bytes, stt_available)` (ligne 860)
- Protection : 10 erreurs consécutives → `RuntimeError` (lignes 887-888)
- Remet le stream micro en marche si crashé pendant TTS (lignes 880-885)

---

## Étape 5 — PCM → WAV

- **Fichier** : `scripts/audio_daemon.py` lignes 1367-1375
- **Fonction** : `_pcm_to_wav(pcm_bytes)` (ligne 1367)
- **Conversion** : `io.BytesIO()` + `wave.writeframes(pcm_bytes)` — génère un header WAV (44 octets) suivi du PCM brut.
- Aucune re-encode, juste encapsulation.
- Appelée ligne 913 : `wav_bytes = self._pcm_to_wav(pcm_bytes)`

---

## Étape 6 — STT (Speech-to-Text)

### Sélection du moteur

- **Fichier** : `scripts/audio_daemon.py` lignes 912-949
- **Priorité 1** : ancien fournisseur audio cloud ancien STT cloud (cloud) — import `audio.stt.stt`, vérifie `stt.available` (lignes 917-931)
- **Priorité 2 (fallback)** : faster-whisper (local) — si ancien fournisseur audio cloud échoue ou pas de texte retourné (lignes 934-949)
- **Priorité 3** : pas de texte → skip silencieux (ligne 959-964)

### ancien fournisseur audio cloud ancien STT cloud (défaut)

- **Fichier** : `audio/stt.py` lignes 1-87
- **Disponibilité** : `LEGACY_STT_API_KEY` définie dans `.env` → `stt.available = True` (ligne 23)
- **Format accepté** : WebM, Opus, MP3, WAV, OGG… (ligne 4). Le MIME envoyé est `audio/webm` (ligne 47) — bien que les bytes soient du WAV.
- **Appel API** : `POST https://api.retired-audio-provider.invalid/v1/speech-to-text` (ligne 45)
- **Modèle** : `legacy_stt_v2` (ligne 49)
- **Options** : `tag_audio_events: false` (ligne 52) — évite les sorties "(musique)", "(rire)"
- **Timeout** : 30.0s (ligne 41, défaut)
- **Nettoyage** : regex `_AUDIO_EVENT_TAG_RE` supprime les éventuels tags restants (ligne 62)
- **Coût** : inclus dans le forfait ancien fournisseur audio cloud (0 token API Claude/DeepSeek)
- **Latence typique** : ~300-500ms (cloud)

### faster-whisper (fallback local)

- **Fichier** : `audio/stt_local.py` lignes 1-166
- **Activation** : nécessite `AUDIO_DAEMON_STT_ENGINE=local` dans `.env` (ligne 39). Actuellement vide → `available = False` (ligne 39)
- **Modèle par défaut** : `tiny` (75 Mo) (ligne 25)
- **Latence typique** : ~50-100ms (local, zero réseau)

---

## Étape 7 — Filtres post-STT

- **Fichier** : `scripts/audio_daemon.py` lignes 959-979
- **Filtres** (dans l'ordre) :
  1. **STT indisponible** : si ni ancien STT cloud ni faster-whisper → skip (lignes 960-964)
  2. **Résidu d'écho** : transcription < 10 chars ET moins de 2s depuis le dernier TTS → ignoré (lignes 967-972)
  3. **Transcription trop courte** : < 3 chars → ignoré (lignes 974-978)
- **Broadcast transcript** : `await self._broadcast_state({"transcript": text})` (ligne 983)
- **Phrases de fin** : 13 patterns détectés (lignes 50-55) :
  ```python
  ("merci jarvis", "c'est bon jarvis", "c'est tout jarvis",
   "merci c'est bon", "c'est fini", "bonne nuit jarvis",
   "a plus jarvis", "ok merci", "au revoir", "stop",
   "arrête", "arrête-toi")
  ```
  → Joue "Bien Monsieur, je reste en veille." + son de fin (lignes 987-994)

---

## Étape 8 — LLM (DeepSeek)

- **Fichier** : `main.py` lignes 3630-3842
- **Fonction** : `_process_voice_fast(text, conversation_id)` (ligne 3630)
- **Bypass l'orchestrateur** : n'utilise pas le routing multi-agents. Appel direct DeepSeek flash.

### Pass 1 — Décision (réponse directe OU action)

- **Horodatage** : `_get_horodatage()` depuis `agents/__init__.py` ligne 20-33. Format : `[HORODATAGE] lundi 29 janvier 2026, 18:30 — Europe/Paris`
- **Historique** : 10 derniers messages de la conversation via `get_conversation_history(conversation_id, limit=10)` (lignes 3656-3665). **Pas** de `build_full_context()` — pas de life profile, pas de people, pas de patterns.
- **System prompt** : lignes 3668-3704. Contient :
  - Nom de l'utilisateur (`config.USER_NAME`)
  - Horodatage
  - Ville météo (`config.WEATHER_CITY`)
  - **Règles strictes orales** : 1-3 phrases, pas de Markdown, pas de "je reviens"
  - **17 actions disponibles** avec leur format JSON
  - **La persona.txt n'est PAS chargée** (optimisation latence)
- **Modèle** : `config.DEEPSEEK_FAST_MODEL` = `"deepseek-v4-flash"` (`.env` ligne 15)
- **Appel** : `llm.chat(messages, model=config.DEEPSEEK_FAST_MODEL, system=system, max_tokens=300, temperature=0.7)` (lignes 3711-3717)
- **Timeout** : 120.0s (httpx, `llm.py` ligne 90)
- **Détection bloc action** : 2 patterns regex (lignes 3738-3741) :
  1. `` ```action {json} ``` `` (format LLM standard)
  2. `action {"type": "..."}` (JSON brut sans backticks)
- **Extraction émotion** : regex `^\[(\w+)\]\s*\n?` (lignes 3732-3735) — tag `[warm]`, `[serious]`, etc. en début de réponse

### Pass 2 — Reformulation (si action exécutée)

- **Exécution action** : `action_result = await execute_action(action)` (ligne 3766) — `actions.py` ligne 24
- **Fallback si JSON invalide ou exception** : `action_result = {"ok": False, "error": ...}` (lignes 3767-3772)
- **Messages pass 2** : historique + message user + `[Action executée : type]` + `result_summary[:800]` + instruction de reformulation (lignes 3781-3793)
- **System pass 2** : simplifié, pas d'actions listées (lignes 3795-3799)
- **Appel** : `llm.chat(pass2_messages, model=config.DEEPSEEK_FAST_MODEL, system=pass2_system, max_tokens=200, temperature=0.7)` (lignes 3802-3808)
- **Fallback LLM pass 2 échoue** : `_fallback_action_response(action_type, action_result)` (lignes 3821-3826, 3845+)
  - `_fallback_action_response` gère sans LLM : `weather`, `open_app`, `task`, `calendar`, `terminal`, `reminder`, `mood` — formats de réponse prédéfinis

### Coût et latence

| Mode | Appels DeepSeek | max_tokens | Latence estimée |
|------|-----------------|------------|-----------------|
| Réponse directe | 1 | 300 | ~500-800ms |
| Avec action | 2 | 300 + 200 | ~1.2-2.0s |
| Action + fallback | 1 | 300 | ~600ms |

---

## Étape 9 — Exécution action

- **Fichier** : `actions.py` ligne 24
- **Fonction** : `async def execute_action(action: dict) -> dict`
- **17 types d'action** listés dans le system prompt (lignes 3682-3699) :
  `weather`, `open_app`, `task`, `reminder`, `calendar`, `calendar_create`, `terminal`, `mood`, `mail`, `mail_read`, `note`, `find_file`, `clipboard`, `system_info`, `name_place`, `where_am_i`, `day_route`, `search_conversations`
- Chaque action a sa fonction `_action_{type}` dédiée

---

## Étape 10 — TTS (Text-to-Speech)

### Sélection (audio_daemon)

- **Fichier** : `scripts/audio_daemon.py` lignes 1266-1309
- **Fonction** : `_play_tts(text, emotion)` (ligne 1266)
- **Priorité 1** : Edge TTS (`audio/tts.py` — `_get_tts("edge")`) — lignes 1279-1284
- **Priorité 2** : macOS natif (`say + afconvert`, `macos_tts`) — lignes 1287-1295
- **Priorité 3** : `say` direct — lignes 1297-1304

### Edge TTS (défaut actuel)

- **Fichier** : `audio/tts.py` lignes 156-177
- **Voix** : `fr-FR-HenriNeural` (`.env` ligne 32, `config.TTS_VOICE`)
- **Fonctionnement** : `edge_tts.Communicate(text, voice)` → `communicate.save(tmp_path)` → écrit MP3 dans un fichier temporaire → lit les bytes → supprime le fichier
- **Latence typique** : ~200-300ms (réseau Microsoft Edge)

### macOS TTS (fallback)

- **Fichier** : `audio/tts.py` (classe `MacOSTTSEngine`)
- **Voix** : `Thomas` (`.env` ligne 33, `config.MACOS_TTS_VOICE`)

---

## Étape 11 — Playback

- **Fichier** : `scripts/audio_daemon.py` lignes 1311-1340
- **Fonction** : `_play_audio_local(audio_bytes)` (ligne 1311)
- **Détection format** : `RIFF` → `.wav`, `ID3`/`0xFF 0xFB` → `.mp3`, sinon → `.m4a`
- **Lecture** : `NamedTemporaryFile` → `afplay` (subprocess macOS natif) → `await proc.wait()` → `unlink(missing_ok=True)`
- **Micro coupé pendant playback** : OUI. Avant TTS, `self._stream.stop_stream()` est appelé (lignes 1041-1045)
- **Anti-écho** : pendant le TTS, les frames micro sont jetées (`if self._tts_playing: continue` — ligne 561)

---

## Étape 12 — Purge post-TTS + reprise micro

- **Fichier** : `scripts/audio_daemon.py` lignes 1052-1086
- **Séquence** :
  1. `await asyncio.sleep(0.5)` — attendre que l'écho acoustique s'éteigne (ligne 1054)
  2. Drainer `_audio_queue` — jeter tous les résidus du TTS captés par le micro (lignes 1056-1064)
  3. Drainer `_utterance_queue` — jeter les phrases en attente (lignes 1067-1073)
  4. `self._stream.start_stream()` — rouvrir le micro (lignes 1076-1080)
  5. `self._tts_playing = False`, `self._last_tts_end = time.time()` (lignes 1082-1083)
  6. Reset timeout conversation (ligne 1084)
  7. `state → "listening"` + broadcast (lignes 1085-1086)

---

## Étape 13 — Sauvegarde DB

- **Fichier** : `main.py` lignes 3969-3985
- **Fonction** : `_save_voice_messages(conversation_id, user_text, assistant_text, cost)` (ligne 3969)
- Sauvegarde 2 messages : `"user"` + `"assistant"` avec `agent="voice"` et `model=config.DEEPSEEK_FAST_MODEL`
- Appelée après chaque `_process_voice_fast()` — lignes 3747 (réponse directe) et 3829 (après action)
- Conversation ID : créé dans `_process_single_utterance` via `create_conversation(agent="daemon_audio")` ligne 1008 si `self._conv_id is None`

---

## Étape 14 — Robustesse & watchdogs

### Boucle immortelle

- **Fichier** : `scripts/audio_daemon.py` lignes 192-250
- **Backoff exponentiel** : 3s → 4.5s → 6.8s → … → 30s max (lignes 214-215, 235)
- **Abandon temporaire** : après 10 crashes consécutifs → sleep 300s (lignes 241-248)

### Watchdog micro

- **Fichier** : `scripts/audio_daemon.py` lignes 1090-1135
- **Intervalle** : 10s (ligne 1097)
- **Détection** : si `_audio_queue.empty()` pendant 60s (6×10s) → `RuntimeError("Micro silencieux")` (lignes 1116-1123)

### Watchdog stream inactif

- **Fichier** : `scripts/audio_daemon.py` lignes 1107-1113
- Si `_stream.is_active()` est False → tentative `start_stream()`, si échec → restart complet

---

## Latence totale mesurée

| Étape | Latence estimée | Source |
|-------|----------------|--------|
| Capture micro (1 chunk) | 30ms | `CHUNK_MS = 30` |
| VAD → fin phrase | 0-1500ms | silence 1.5s après parole |
| PCM → WAV | <1ms | encapsulation header |
| STT ancien fournisseur audio cloud ancien STT cloud | ~300-500ms | cloud (API HTTP) |
| STT faster-whisper tiny | ~50-100ms | local ONNX |
| LLM pass 1 (DeepSeek flash) | ~500-800ms | API HTTP, 300 tokens max |
| Action (si applicable) | variable | dépend de l'action |
| LLM pass 2 (si action) | ~400-600ms | API HTTP, 200 tokens max |
| TTS Edge | ~200-300ms | HTTP + synthèse Edge |
| TTS macOS | ~100-200ms | local `say` |
| Playback `afplay` | dépend durée texte | ~1.5s pour 1 phrase |
| Purge post-TTS | 500ms | `sleep(0.5)` |
| **TOTAL (réponse directe)** | **~1.0-1.5s** | STT + LLM pass 1 + TTS + playback |
| **TOTAL (avec action)** | **~2.0-3.0s** | STT + LLM×2 + action + TTS + playback |

---

## Points de défaillance

| Point | Risque | Protection | Fichier:ligne |
|-------|--------|-----------|---------------|
| Queue audio pleine | Perte de frames | `maxsize=300` + drain intelligent à 100 frames | audio_daemon.py:565-583 |
| STT ancien fournisseur audio cloud timeout | Pas de transcription | Timeout 30s, fallback faster-whisper | stt.py:41, audio_daemon.py:934-949 |
| STT ancien fournisseur audio cloud erreur HTTP | Pas de transcription | try/except, fallback local | audio_daemon.py:930-931 |
| LLM pass 1 échoue | Pas de réponse | try/except → message d'erreur générique | main.py:3720-3728 |
| LLM pass 2 échoue | Réponse vide après action | `_fallback_action_response()` | main.py:3821-3827 |
| Action JSON invalide | Exception JSONDecodeError | try/except → `{"ok": False}` | main.py:3767-3769 |
| TTS Edge échoue | Pas d'audio | Fallback macOS natif → `say` direct | audio_daemon.py:1287-1304 |
| TTS `afplay` échoue | Pas de son | try/except, log warning | audio_daemon.py:1333-1334 |
| Micro déconnecté USB | Plus de frames | Watchdog 10s → détection 60s silence → restart | audio_daemon.py:1090-1135 |
| Stream micro inactif | Plus de frames | Tentative `start_stream()` → restart | audio_daemon.py:1107-1113 |
| Crash VAD loop | Plus de traitement | 50 erreurs → `RuntimeError` → restart `_run()` | audio_daemon.py:807-811 |
| Crash process loop | Plus de processing | 10 erreurs → `RuntimeError` → restart | audio_daemon.py:887-888 |
| Daemon crash complet | Plus d'audio | Boucle immortelle, backoff exponentiel | audio_daemon.py:218-249 |
| Résidu d'écho TTS | Fausse transcription | Filtre < 10 chars dans les 2s post-TTS | audio_daemon.py:967-972 |
| Conversation ID NULL | DB FK violation | Créé au premier message si absent | audio_daemon.py:1007-1008 |
| Permission macOS micro | Micro muet | Détection RMS ≤ 0.0001 pendant 3s + log CRITICAL | audio_daemon.py:539-556 |

---

## Variables de configuration

| Variable | Valeur actuelle | Fichier:ligne |
|----------|----------------|---------------|
| `DEEPSEEK_FAST_MODEL` | `deepseek-v4-flash` | `.env:15`, `config.py:20` |
| `DEEPSEEK_MAIN_MODEL` | `deepseek-v4-pro` | `.env:16`, `config.py:21` |
| `VOICE_MAX_TOKENS` | `500` | `.env:39`, `config.py:36` |
| `TTS_ENGINE` | `edge` | `.env:29`, `config.py:27` |
| `TTS_VOICE` | `fr-FR-HenriNeural` | `.env:32`, `config.py:28` |
| `LEGACY_STT_API_KEY` | `sk_...` | `.env:27` |
| `AUDIO_DAEMON_ENABLED` | `true` | `.env:88` |
| `AUDIO_DAEMON_SPEECH_THRESHOLD` | `0.02` | `.env:89` |
| `AUDIO_DAEMON_SILENCE_MS` | `1500` | `.env:90` |
| `AUDIO_DAEMON_MIN_SPEECH_MS` | `600` | `.env:91` |
| `AUDIO_DAEMON_MAX_UTTERANCE_S` | `15` | `.env:92` |
| `AUDIO_DAEMON_CONVERSATION_TIMEOUT` | `45.0` | `.env:93` |
| `AUDIO_DAEMON_INPUT_DEVICE` | `Blue Snowball` | `.env:94` |
| `WAKE_WORD_ENABLED` | `false` | `.env:96` |
| `AUDIO_DAEMON_STT_ENGINE` | _(vide)_ | `.env:99` |
| `AUDIO_DAEMON_STT_MODEL` | _(vide)_ | `.env:100` |
| `LANGUAGE` | `fr` | `.env:74` |
| `USER_NAME` | `Elias` | `.env:73` |
| `WEATHER_CITY` | `Lille` | `.env:49` |

---

## Flux de données complet (types Python)

```
bytes (PCM 16-bit 16kHz mono, 960 octets/chunk)
  │ [stream.read(CHUNK_SAMPLES)] — audio_daemon.py:544
  ▼
asyncio.Queue[bytes] (maxsize=300)
  │ [_safe_put via call_soon_threadsafe] — audio_daemon.py:565-585
  ▼
list[bytes] (_vad_loop_safe: frames accumulator)
  │ [VAD: RMS > 0.02 → parole, silence 1.5s → fin] — audio_daemon.py:619-785
  ▼
bytes (b"".join(frames) — utterance PCM complète)
  │ [utterance_queue.put_nowait] — audio_daemon.py:774-785
  ▼
bytes (wav_bytes = _pcm_to_wav(pcm) — encapsulation WAV)
  │ [io.BytesIO + wave] — audio_daemon.py:913, 1367-1375
  ▼
bytes (wav → ancien fournisseur audio cloud ancien STT cloud POST /v1/speech-to-text)
  │ [legacy_stt_v2, audio/webm MIME] — stt.py:44-54
  │ ou fallback: wav → faster-whisper local — stt_local.py:60+
  ▼
str (transcription) — ex: "quel temps fait-il ?"
  │ [filtres: < 3 chars skip, résidu écho skip] — audio_daemon.py:959-979
  ▼
_dict_ (résultat _process_voice_fast)
  │ Pass 1: llm.chat(messages, deepseek-v4-flash, max_tokens=300)
  │ [regex détection ```action {json}``` ou action {"type":...}]
  │ Si pas d'action → retour direct
  │ Si action → execute_action() → Pass 2: llm.chat(max_tokens=200)
  │ main.py:3630-3842
  ▼
  ├─ str (response_text) — ex: "Il fait 18 degrés à Lille, couvert."
  ├─ str (emotion) — ex: "warm" (extrait du tag [warm] ligne 1)
  ├─ float (cost) — coût total DeepSeek
  ├─ dict|None (action_result)
  └─ float (latency_ms)
  ▼
bytes (audio TTS → Edge: MP3, macOS: M4A)
  │ TTS: edge_tts.Communicate(text, voice).save(tmp.mp3)
  │ ou macos_tts.synthesize(text) — tts.py:1281-1294
  ▼
subprocess: afplay tmp.mp3 (macOS)
  │ audio_daemon.py:1326-1331
  ▼
Purge + reprise micro
  │ sleep(0.5) → drain queues → stream.start_stream()
  │ audio_daemon.py:1052-1086
```

---

## Réponses aux 25 questions

1. **Quel format audio le micro produit-il ?**
   → PCM 16-bit signed, 16 kHz, mono. Chunk 480 samples (30ms, 960 bytes). `audio_daemon.py:37-42`

2. **Comment le callback pyaudio thread-safe envoie-t-il les frames à la coroutine async ?**
   → `loop.call_soon_threadsafe(_safe_put, data)` (ligne 585). `_safe_put` est définie comme closure dans le thread (lignes 565-583) et utilise `audio_queue.put_nowait()`.

3. **Quelle est la taille max de la queue audio ?**
   → `maxsize=300` (ligne 280) soit 300 × 960 = 288 KB, ~9s d'audio.

4. **Comment le VAD détecte-t-il la fin de phrase ?**
   → RMS > 0.02 = parole. Silence ≥ 50 chunks (1500ms). Si parole < 2s, silence exigé passe à 66 chunks (2000ms). `audio_daemon.py:606, 611, 754-762`

5. **Les frames sont-elles converties en WAV avant le STT ?**
   → OUI. `_pcm_to_wav(pcm_bytes)` (lignes 1367-1375), appelée ligne 913. `io.BytesIO()` + `wave` — pas de ré-encodage, juste un header WAV prepended.

6. **Quel STT est utilisé par défaut ?**
   → **ancien fournisseur audio cloud ancien STT cloud** (cloud). `audio/stt.py:31-84`. `legacy_stt_v2`, appelé ligne 927 dans `_process_single_utterance`.

7. **Comment le choix STT est-il fait ?**
   → Priorité 1 : `audio.stt.stt.available` (si `LEGACY_STT_API_KEY` définie). Priorité 2 : `audio.stt_local.stt_local.available` (si `AUDIO_DAEMON_STT_ENGINE=local`). `audio_daemon.py:917-949`

8. **Le STT reçoit-il du WAV, du PCM brut, du WebM ?**
   → Le STT reçoit du **WAV** (issu de `_pcm_to_wav`). Le MIME envoyé à ancien fournisseur audio cloud est `audio/webm` (ligne 47) — mais le contenu est du WAV.

9. **`_process_voice_fast` est-il bien appelé (et pas `_process_message_internal`) ?**
   → OUI. Ligne 1012 : `result = await _process_voice_fast(text, self._conv_id)`. C'est bien le pipeline rapide, pas `_process_message_internal`. `audio_daemon.py:1011-1012`

10. **Quel modèle DeepSeek est utilisé en vocal ?**
    → `config.DEEPSEEK_FAST_MODEL` = `"deepseek-v4-flash"`. `main.py:3713, 3804`. `.env:15`

11. **Le system prompt vocal contient-il l'horodatage ?**
    → OUI. `horodatage = _get_horodatage()` (ligne 3651), injecté dans le system prompt : `DATE ET HEURE : {horodatage}` (ligne 3678). Format : `[HORODATAGE] lundi 29 janvier 2026, 18:30 — Europe/Paris`

12. **Le system prompt vocal contient-il la persona.txt ?**
    → NON. Le system prompt est construit manuellement (lignes 3668-3704) sans charger `prompts/persona.txt`. Le comportement JARVIS est reproduit via des règles inline : "Appelle-le Monsieur", "pas de Markdown", "concis". **Optimisation latence** : la persona.txt fait plusieurs centaines de tokens.

13. **Combien de tokens max en vocal ?**
    → `max_tokens=300` pour le pass 1 (ligne 3715), `max_tokens=200` pour le pass 2 (ligne 3806). La variable `VOICE_MAX_TOKENS=500` (`.env:39`) n'est **PAS utilisée** par le pipeline vocal daemon — elle est utilisée par la page `/voice` web (mains libres navigateur).

14. **L'historique de conversation est-il chargé ?**
    → OUI. 10 derniers messages via `get_conversation_history(conversation_id, limit=10)` (lignes 3656-3665). Si le dernier message est de l'utilisateur, il est retiré (ligne 3662-3663). Le `conversation_id` est créé via `create_conversation(agent="daemon_audio")` (ligne 1008).

15. **Comment les actions sont-elles détectées ?**
    → 2 patterns regex dans `_process_voice_fast` (lignes 3738-3741) :
    - `` ```action\s*(\{.*?\})\s*(?:```|$) `` — bloc code action JSON
    - `` action\s*(\{"type":\s*"[^"]+.*?\}) `` — JSON brut

16. **Que se passe-t-il quand une action est détectée ?**
    → Pipeline 2 passes (lignes 3761-3842) :
    1. `action = json.loads(action_match.group(1))` — parse JSON
    2. `action_result = await execute_action(action)` — exécute l'action
    3. Pass 2 LLM : reformule le résultat en réponse naturelle
    4. Si LLM pass 2 échoue ou réponse vide → `_fallback_action_response()`

17. **Le fallback action existe-t-il ?**
    → OUI. `_fallback_action_response(action_type, action_result)` lignes 3845+. Gère sans LLM : `weather`, `open_app`, `task`, `calendar`, `terminal`, `reminder`, `mood`. Appelé si pass 2 LLM échoue (ligne 3826) ou réponse vide (ligne 3822).

18. **Quel TTS est utilisé ?**
    → **Edge TTS**. `scripts/audio_daemon.py:1279-1284` appelle `_get_tts("edge")`. La config `TTS_ENGINE=edge` (`.env:29`) est lue par `audio/tts.py` ligne 51. Fallback : macOS `say` + afconvert (ligne 1287-1295).

19. **Comment le TTS est-il joué ?**
    → `afplay` (commande macOS native) via subprocess. `audio_daemon.py:1326-1331` :
    ```python
    self._tts_proc = await asyncio.create_subprocess_exec("afplay", tmp_path, ...)
    await self._tts_proc.wait()
    ```

20. **Le micro est-il coupé pendant le TTS ?**
    → OUI. `self._stream.stop_stream()` est appelé avant le TTS (lignes 1041-1045). De plus, `self._tts_playing = True` (ligne 1277) bloque l'insertion de nouvelles frames dans la queue (ligne 561).

21. **Que se passe-t-il après le TTS ?**
    → 6 étapes (lignes 1052-1086) :
    1. sleep 0.5s (anti-écho)
    2. Drain audio_queue
    3. Drain utterance_queue
    4. stream.start_stream() (reprise micro)
    5. Reset flags + timeout
    6. State → "listening" + broadcast

22. **Le daemon redémarre-t-il automatiquement après un crash ?**
    → OUI. Boucle immortelle avec backoff exponentiel (3s → 30s max). Après 10 crashes → abandon 5 minutes. `audio_daemon.py:214-248`

23. **Le watchdog micro existe-t-il ?**
    → OUI. Toutes les 10s, vérifie : stream actif, frames dans la queue. 60s sans frame → `RuntimeError` → restart. `audio_daemon.py:1090-1135`

24. **Le broadcast WebSocket fonctionne-t-il ?**
    → OUI. À chaque changement d'état, `_broadcast_state()` est appelé (lignes 1441-1456). Le callback `_broadcast` est injecté par `main.py` via `audio_daemon.set_broadcast(broadcast_ws)` (ligne 705 de main.py). Les événements incluent : transcript, response, state changes, emotion.

25. **Les messages sont-ils sauvegardés en DB ?**
    → OUI. `_save_voice_messages()` sauve 2 messages (`user` + `assistant`) avec `agent="voice"` et `model=config.DEEPSEEK_FAST_MODEL`. Le `conversation_id` est créé au premier message via `create_conversation(agent="daemon_audio")`. `main.py:3969-3985`, `audio_daemon.py:1007-1008`

---

## Ce qui n'est PAS dans le pipeline vocal daemon

Contrairement au pipeline texte (`_process_message` → orchestrateur → agent spécialisé), le pipeline vocal daemon :

- **Ne passe PAS par l'orchestrateur** : pas de classification SCHOOL/PRODUCTIVITY/COACH/INFO/JOURNAL
- **Ne charge PAS `prompts/persona.txt`** : règles inline pour économiser ~1000 tokens
- **N'utilise PAS `VOICE_MAX_TOKENS`** : utilise `max_tokens=300` (pass 1) et `200` (pass 2) hardcodés
- **N'appelle PAS `build_full_context()`** : pas de life profile, pas de people memory, pas de patterns
- **N'utilise PAS le prompt caching** DeepSeek (le cache est automatique côté serveur, pas d'API explicite)
- **Ne fait PAS de streaming** : la réponse est générée en une fois, le TTS est joué après
- **Ne fait PAS de reformulation après action** via `_process_message_internal` : utilise son propre pass 2
