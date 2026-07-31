<!--
source_agent: bc-019fb86f-7099-70a4-8e3b-8fc13574cec2
agent_name: Pipeline audio revue
agent_url: https://cursor.com/agents/bc-019fb86f-7099-70a4-8e3b-8fc13574cec2
agent_status: IDLE
created_at: 2026-07-31T13:49:05.009000+00:00
extracted_msg_index: 116
extracted_at: 2026-07-31T14:37:19.332878+00:00
-->

# AUDIT — P09 — Audio et TTS/STT

## Métadonnées
- Agent / modèle : Auto (Composer) — auditeur pipeline audio
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `99a9b71833ceb457e8315efbda67982942e14dba`
- Branche : `main`
- Fichiers dans le périmètre (count) : 24 sources existants + `models/kokoro/` absent
- Fichiers lus (count) : 24
- Couverture estimée : 100 % des sources présentes ; 0 % binaires modèle (absents)

## Synthèse exécutive
Le contrat « STT local uniquement, pas de repli cloud STT » est tenu dans `audio/`, `native_audio/` et `scripts/audio_daemon.py` : chaîne faster-whisper / WhisperKit / whisper.cpp, décodage média local, TTS natif daemon sans Edge. Kokoro MLX sépare correctement logs (stderr) et audio WAV (stdout). Sélection micro Snowball/auto est en place ; sortie audio = défaut système (`sounddevice` puis `afplay`). Points faibles : half-duplex qui `stop_stream()` pendant qu’un thread lit encore le micro ; émotions TTS déclarées mais non appliquées à la synthèse ; filtre `is_stt_prompt_echo` absent du daemon natif ; caches TTS / verrou preload STT non thread-safe. `models/kokoro/` n’existe pas dans ce checkout.

## Findings
### F-P09-001
- Sévérité : HIGH
- Type : bug
- Titre : Half-duplex — `stop_stream()` concurrent au thread micro provoque la mort du capture
- Preuve : `scripts/audio_daemon.py:1368-1372` + `795-809`
```python
if self._half_duplex and self._stream:
    self._stream.stop_stream()
# … pendant que le thread fait :
data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
except OSError as e:
    … break  # sortie définitive du thread input
```
- Impact : après un tour TTS (défaut `AUDIO_DAEMON_HALF_DUPLEX=True`), le lecteur PortAudio peut quitter ; micro mort jusqu’au restart watchdog / boucle immortelle (dizaines de secondes).
- Repro / condition : daemon actif, half-duplex on, une utterance → TTS → `stop_stream` pendant `read`.
- Correctif proposé (sans coder) : ne jamais `stop_stream` depuis le process loop ; s’appuyer uniquement sur `_tts_playing_event` pour ignorer les frames, ou ouvrir/fermer le stream dans le même thread que `read`.
- Confiance : haute

### F-P09-002
- Sévérité : HIGH
- Type : contrat-cassé
- Titre : Paramètre `emotion` TTS accepté puis ignoré par tous les moteurs
- Preuve : `audio/tts.py:55-66` (validation puis `_synth_edge(text)` sans emotion) ; `audio/tts.py:277-296` (Kokoro) ; `audio/tts.py:363-373` (macOS) ; `native_audio/ttskit_mlx.py:102-108` (`instruct=None` explicite)
- Impact : tags `[warm]`/`[urgent]` etc. n’influencent ni débit, ni pitch, ni voix — contrat CLAUDE.md / persona non honoré côté synthèse.
- Repro / condition : tout appel `synthesize(text, emotion="urgent")`.
- Correctif proposé (sans coder) : Edge SSML rate/pitch par émotion ; macOS `say -r` ; Kokoro/TTSKit mapping émotion→speed/instruct documenté ; ou retirer le contrat émotion du pipeline natif.
- Confiance : haute

### F-P09-003
- Sévérité : MEDIUM
- Type : bug
- Titre : Daemon natif n’applique pas `is_stt_prompt_echo` (contrairement au mobile)
- Preuve : `audio/stt_daemon.py:82-121` (filtre défini) ; `scripts/audio_daemon.py` — aucune occurrence ; usage hors périmètre `api/mobile_voice_service.py` (P04)
- Impact : sur silence/bruit, Whisper peut republier le `initial_prompt` ; le daemon ne rejette que ghosts YouTube + `avg_logprob`, pas l’écho de prompt.
- Repro / condition : utterance quasi silencieuse avec STT local + prompt FR.
- Correctif proposé (sans coder) : après transcription daemon, appeler `is_stt_prompt_echo(text)` et jeter comme le mobile.
- Confiance : haute

### F-P09-004
- Sévérité : MEDIUM
- Type : perf
- Titre : WAV RIFF traité comme conteneur compressé → re-décodage inutile
- Preuve : `audio/audio_format.py:25-26` (`RIFF` → True) ; `audio/stt_daemon.py:651-653` (branche decode si encoded)
- Impact : latence STT accrue (ffmpeg/`decode_audio`) pour tout WAV déjà PCM, y compris chemins qui enverraient du RIFF.
- Repro / condition : `DaemonSTT.transcribe(wav_bytes)`.
- Correctif proposé (sans coder) : exclure RIFF/WAV de `is_encoded_audio_container` ; decoder seulement WebM/MP3/OGG/M4A.
- Confiance : haute

### F-P09-005
- Sévérité : MEDIUM
- Type : smell
- Titre : `FasterWhisperBackend._load_lock` créé jamais utilisé — course au preload
- Preuve : `audio/stt_daemon.py:164` ; `166-207` / `216-217` (`preload_sync` sans `async with self._load_lock`)
- Impact : deux `transcribe_pcm` concurrentes au premier appel peuvent double-charger le modèle ou laisser `_load_failed` incohérent.
- Repro / condition : deux transcriptions async avant fin du premier preload.
- Correctif proposé (sans coder) : protéger `_loaded`/`_model` par le lock (sync `threading.Lock` dans l’executor).
- Confiance : moyenne

### F-P09-006
- Sévérité : MEDIUM
- Type : smell
- Titre : Caches TTS process-wide sans synchronisation
- Preuve : `audio/tts_cache.py:63-75` (`LastTTS._entry`) ; `78-124` (`SpeculativeTTS._cache` muté sans lock)
- Impact : daemon + WS (P04) peuvent lire/écrire concurremment → audio « répète » corrompu ou cache partiel.
- Repro / condition : `last_tts.store` pendant un `get` depuis un autre chemin async/thread.
- Correctif proposé (sans coder) : `threading.Lock` ou structure immutable copy-on-write.
- Confiance : moyenne

### F-P09-007
- Sévérité : MEDIUM
- Type : bug
- Titre : File d’utterances pleine → phrase jetée sans feedback utilisateur
- Preuve : `scripts/audio_daemon.py:1016-1020`
```python
except asyncio.QueueFull:
    logger.warning("[audio_daemon] utterance_queue pleine — utterance jetée")
```
- Impact : sous charge (LLM lent), parole utilisateur perdue silencieusement (log only).
- Repro / condition : `maxsize=3`, 4 phrases rapides pendant `processing`.
- Correctif proposé (sans coder) : drop oldest + bip / TTS court « Je n’ai pas tout saisi », ou backpressure VAD.
- Confiance : haute

### F-P09-008
- Sévérité : LOW
- Type : dette
- Titre : Pas de sélection de périphérique de sortie (seulement entrée Snowball)
- Preuve : `scripts/audio_daemon.py:1828-1876` (input only) ; `audio/audio_output.py:96-97` / `161-163` (`OutputStream` sans `device=`)
- Impact : checklist « output device » non couverte — lecture toujours sur défaut système (AirPods etc.).
- Repro / condition : multi-périphériques audio macOS.
- Correctif proposé (sans coder) : `AUDIO_DAEMON_OUTPUT_DEVICE` + `sounddevice`/`sd.default.device`.
- Confiance : haute

### F-P09-009
- Sévérité : LOW
- Type : dead-code
- Titre : Chemins Porcupine / volume wake séparés morts
- Preuve : `scripts/audio_daemon.py:1534-1538` (`_start_wake_detection` no-op) ; `1554-1646` (boucles encore présentes)
- Impact : maintenance trompeuse ; wake réel = volume sur flux unique (`934-964`).
- Repro / condition : lecture du code.
- Correctif proposé (sans coder) : supprimer ou isoler derrière un flag testé.
- Confiance : haute

### F-P09-010
- Sévérité : LOW
- Type : sécurité
- Titre : Silero VAD charge via `torch.hub` (réseau possible au boot)
- Preuve : `audio/vad_silero.py:81-86` (`torch.hub.load(..., trust_repo=True)`)
- Impact : hors contrat STT cloud, mais téléchargement réseau au démarrage VAD ; `trust_repo=True` élargit la surface.
- Repro / condition : premier boot sans cache hub, torch installé.
- Correctif proposé (sans coder) : bundle local du modèle Silero, `local_files_only` / chemin offline.
- Confiance : haute

### F-P09-011
- Sévérité : INFO
- Type : doc-drift
- Titre : `models/kokoro/` absent du checkout — ONNX non auditable
- Preuve : `audio/tts.py:150-151` / `engine_config.py:12` pointent vers `models/kokoro/` ; `find` → répertoire inexistant
- Impact : backend `KOKORO_BACKEND=onnx` toujours `available=False` ici ; seuls MLX/macOS/Edge restent.
- Repro / condition : clone sans artefacts modèle.
- Correctif proposé (sans coder) : documenter setup ONNX obligatoire ; CI skip ou fixture minimale non binaire.
- Confiance : haute

### F-P09-012
- Sévérité : INFO
- Type : smell
- Titre : Erreurs TTS/STT souvent avalées en `b""` / `None` + log (pas de crash)
- Preuve : `audio/tts.py:109-111`, `419-454` (stderr `DEVNULL` sur `say`/`afconvert`) ; `audio/stt_daemon.py:271-273`, `631-633`
- Impact : robustesse OK (pas de crash) ; diagnostic macOS TTS difficile (pas de stderr).
- Repro / condition : `say` échec voix absente.
- Correctif proposé (sans coder) : capturer stderr sur échec `returncode != 0`, logger les 200 premiers caractères.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. Jamais de repli cloud STT | OK | Aucune ref cloud STT dans `audio/`, `native_audio/`, `audio_daemon.py` ; `FallbackSTTBackend` local-only (`stt_daemon.py:422-471`) ; log `Cloud fallback: disabled` (`engine_config.py:108`) |
| 2. Kokoro MLX : stdout propre + format WAV | OK | Logs → `sys.stderr` (`kokoro_mlx.py:132-136`, `208-210`) ; bridge lit `stdout` (`kokoro_bridge.py:76-89`) ; défaut `--format wav` + header RIFF (`kokoro_mlx.py:94-111`, `169-174`) |
| 3. Sélection input Snowball vs défaut | OK (input) / N/A partiel (output) | Priorité config → Snowball/Shiver → défaut système (`audio_daemon.py:1828-1876`) ; pas de device output (F-P09-008) |
| 4. afplay / afconvert / sounddevice | OK | `sounddevice` prioritaire (`audio_output.py`, `audio_daemon.py:1760-1763`) ; `afplay` fallback + sons wake/end (`1750-1803`) ; `afconvert` dans macOS TTS (`tts.py:407-438`) |
| 5. Émotions TTS | KO | Paramètre présent, synthèse inchangée (F-P09-002) |
| 6. Erreurs silencieuses vs crash | OK avec réserve | Daemon : boucle immortelle + logs ; moteurs : return vide + `logger` ; réserve F-P09-001/007/012 |
| 7. Thread safety / queues | KO partiel | `voice_queue` bien protégé (`asyncio.Lock`/`Condition`) ; micro→queue via `call_soon_threadsafe` OK ; faiblesses F-P09-001/005/006 |
| Décodage WebM/M4A/MP3/OGG local | OK | `DaemonSTT._decode_media_bytes` via `faster_whisper.audio.decode_audio` (`stt_daemon.py:614-633`) |
| TTS daemon sans Edge | OK | `get_native_tts_engine` : kokoro→macos→ttskit uniquement (`tts_native.py:105-132`) ; Kokoro fallback → macOS pas Edge (`tts.py:243-252`) |
| Diarisation | N/A (désactivée) | `transcribe_with_diarization` → `[]` (`stt_daemon.py:669-676`) ; `DIARIZATION_ENABLED` gate (`continuous_recorder.py:286-287`) |

## Frontières / dépendances
- Signale vers P04 : `api/voice_*.py`, `api/mobile_voice_service.py` (consomme `stt_local`, `is_stt_prompt_echo`, TTS web Edge) — non audité en profondeur.
- Signale vers P10 : `scripts/jarvis_daemon.py` file TTS sentinelle ; ici seulement handler `notification.created` → `_play_tts` (`audio_daemon.py:1975-1990`).
- Signale vers P01 : `config.TTS_ENGINE`, `STT_*`, `AUDIO_DAEMON_*`, `KOKORO_*`.
- Signale vers P03/P07 : `process_voice_fast` (`pipeline`) appelé depuis le daemon — hors P09.
- Attendus de ce périmètre consommés ailleurs : `audio.stt` / `tts` (`audio/__init__.py`), `voice_queue`, `native_audio_output`, sidecars `native_audio/*`.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| `models/kokoro/**` (binaires `.onnx`, `voices.bin`, etc.) | Répertoire absent du checkout ; consignes = ignorer gros binaires |
| `native_audio/whisperkit_transcribe` | Binaire sidecar optionnel non présent (seul le bridge Python lu) |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée :
  - `audio/__init__.py`
  - `audio/audio_format.py`
  - `audio/audio_output.py`
  - `audio/continuous_recorder.py`
  - `audio/engine_config.py`
  - `audio/resample.py`
  - `audio/stt_daemon.py`
  - `audio/stt_local.py`
  - `audio/tts.py`
  - `audio/tts_cache.py`
  - `audio/tts_native.py`
  - `audio/vad_silero.py`
  - `audio/vad_utterance.py`
  - `audio/voice_queue.py`
  - `native_audio/__init__.py`
  - `native_audio/README.md`
  - `native_audio/kokoro_bridge.py`
  - `native_audio/kokoro_mlx.py`
  - `native_audio/kokoro_synthesize`
  - `native_audio/ttskit_bridge.py`
  - `native_audio/ttskit_mlx.py`
  - `native_audio/ttskit_synthesize`
  - `native_audio/whisperkit_bridge.py`
  - `scripts/audio_daemon.py`