<!--
source_agent: bc-019fb872-50d1-7794-b5bb-33581c90db94
agent_name: Pipeline audio revue
agent_url: https://cursor.com/agents/bc-019fb872-50d1-7794-b5bb-33581c90db94
agent_status: IDLE
note: doublon ou hors série
-->

```yaml
ID_PERIMETRE: P09
NOM: Audio et TTS/STT
MODE: lecture_seule
DATE: 2026-07-31
VERDICT_GLOBAL: CONDITIONAL_PASS
RESUME: >
  Aucun repli STT cloud dans le périmètre. Kokoro MLX produit un WAV propre
  sans pollution stdout. Les émotions TTS sont inertes. Plusieurs chemins
  avalent les erreurs (WebM fragmenté, whisper.cpp rejeté, TTSKit→Edge web,
  deadlock streamer sounddevice).

PERIMETRE:
  inclus:
    - audio/** (14 modules Python)
    - native_audio/** (bridges + sidecars)
    - scripts/audio_daemon.py (1988 lignes)
    - models/kokoro/ (structure seule)
  exclus_respectes:
    - api/voice_*.py
    - scripts/jarvis_daemon.py (hors appels audio/)
  non_lus:
    - models/kokoro/kokoro-v0_19.onnx (325_525_180 o, binaire)
    - models/kokoro/voices.bin (5_758_648 o, binaire)
    - binaire WhisperKit externe (jarvis-whisperkit) si présent hors dépôt

CARTES_MOTEURS:
  STT: [faster-whisper, WhisperKit sidecar, whisper.cpp, disabled]
  STT_fallback: local_only (jamais réseau)
  TTS_web: [edge|kokoro|macos|ttskit via get_tts_by_name]
  TTS_native: [config → Kokoro → macOS → TTSKit ; jamais Edge]
  decode: faster_whisper.audio.decode_audio (WebM/WAV/MP3/OGG/M4A)
  playback: sounddevice → afplay (défaut système)

CHECKLIST:
  - id: 1
    titre: Jamais de repli cloud STT
    statut: PASS
    preuve:
      - "audio/stt_daemon.py:1 docstring + FallbackSTTBackend:422-425"
      - "audio/engine_config.py:107 Cloud fallback: disabled"
      - "scripts/audio_daemon.py:1176 STT local uniquement"
      - "grep openai/deepgram/httpx/requests dans audio/, native_audio/, audio_daemon.py → 0 hit STT"
    nuances:
      - "STT_ALLOW_MODEL_DOWNLOAD=false par défaut ; download HF = artefact, pas transcription cloud"
      - "WhisperKit sidecar externe NON VÉRIFIABLE (lanceur seul dans le dépôt)"

  - id: 2
    titre: Kokoro MLX — stdout pollué ? format WAV ?
    statut: PASS
    preuve:
      - "native_audio/kokoro_mlx.py:146-178 _silence_stdout() stdout→stderr pendant load/generate"
      - "native_audio/kokoro_mlx.py:95-112 WAV via wave (mono, PCM16 LE, sr MLX défaut 24000)"
      - "native_audio/kokoro_mlx.py:228 écriture binaire exclusive sur stdout.buffer"
    nuances:
      - "TTSKit MLX n'a PAS cette protection (P1 séparé) — hors item Kokoro"

  - id: 3
    titre: Sélection input/output device (Snowball auto vs défaut système)
    statut: PASS
    preuve:
      - "scripts/audio_daemon.py:1834-1874 — 1) AUDIO_DAEMON_INPUT_DEVICE sous-chaîne 2) défaut système 3) 1er micro"
      - "audio/audio_output.py:18-88 — OUTPUT_DEVICE override puis défaut CoreAudio ; pas d'appariement micro→casque"
      - ".env.example:258 AUDIO_DAEMON_INPUT_DEVICE=Blue Snowball (explicite, pas hardcodé)"
    nuances:
      - "Aucune auto-détection Snowball dans le code ; uniquement match de nom si configuré"
      - "AUDIO_DAEMON_OUTPUT_DEVICE absent de .env.example"
      - "Dernier recours entrée : 1er device input sans filtre BlackHole/Soundflower (P3)"

  - id: 4
    titre: afplay / afconvert / sounddevice
    statut: PARTIAL
    preuve:
      - "sounddevice prioritaire : audio/audio_output.py NativeAudioOutput"
      - "afconvert WAVE/LEI16 : audio_output.py:91-118 + tts.py:425-431 (macOS native)"
      - "afplay fallback : audio_daemon.py:1770-1788 — sortie = défaut macOS uniquement"
    ecarts:
      - "Override AUDIO_DAEMON_OUTPUT_DEVICE perdu au repli afplay"
      - "afplay -d = debug, pas sélection device (ne pas l'utiliser comme correctif)"
      - "say/afconvert stderr DEVNULL + pas de timeout dans tts.py:417-431"

  - id: 5
    titre: Émotions TTS
    statut: FAIL
    preuve:
      - "VALID_EMOTIONS acceptées puis ignorées : tts.py:55-66 Edge ignore emotion"
      - "Kokoro synthesize(emotion=...) n'altère ni voice ni speed (tts.py:285-304)"
      - "macOS : pas de say -r / pitch ; TTSKit instruct=None (ttskit_mlx.py:101-108)"
    impact: "warm/serious/concerned/amused/urgent/encouraging = neutral acoustiquement"

  - id: 6
    titre: Gestion erreurs silencieuse vs crash
    statut: PARTIAL
    preuve_positive:
      - "Daemon ne crash pas sur échec STT/TTS (try/except + warning)"
      - "Kokoro → fallback macOS local (jamais Edge)"
    ecarts:
      - "Décodage échec → b'' → silence indistinguable (stt_daemon.py:625-656)"
      - "say/afconvert non-zéro → b'' sans stderr"
      - "Échec pa.open() micro absorbé → daemon vivant mais muet (audio_daemon)"
      - "VAD Silero exception → proba 0.0 sans log (vad_silero.py)"

  - id: 7
    titre: Thread safety / queues
    statut: PARTIAL
    preuve_positive:
      - "VoiceQueue : asyncio.Lock + Condition ; barge-in CRITICAL OK"
      - "Queues micro bornées (300 frames / 3 utterances)"
    ecarts:
      - "P1 deadlock play_stream_from_async si consumer planté (Queue maxsize=16)"
      - "VoiceQueue heap non borné ; timeout wait laisse la requête jouable plus tard"
      - "_load_lock STT déclaré mais jamais utilisé"
      - "Lock audio_output ne sérialise pas sd.play() concurrent (chemin répète)"
      - "Timeout jointure micro 5s puis ferme PyAudio alors que thread peut encore lire"

FINDINGS:
  - sev: P1
    id: P09-F01
    titre: Fragments WebM MediaRecorder transcrits séparément
    where: audio/continuous_recorder.py:235-273 (+ commentaire 278-283)
    detail: >
      timeslice 5s → seul le 1er blob a l'EBML ; suivants traités comme PCM brut
      → hallucinations / perte silencieuse.

  - sev: P1
    id: P09-F02
    titre: whisper.cpp (et WhisperKit segments=[]) rejetés comme low-confidence
    where: scripts/audio_daemon.py:172-179 ; audio/stt_daemon.py:413-414
    detail: >
      _is_low_confidence([]) → True. WhisperCppBackend ne remplit jamais segments.
      Transcriptions valides écartées silencieusement.

  - sev: P1
    id: P09-F03
    titre: get_tts_by_name(ttskit) indisponible → Edge silencieux
    where: audio/tts.py:540-552
    detail: >
      Contredit « Cloud fallback: disabled » pour le chemin web si TTS_ENGINE=ttskit.
      Daemon natif OK (tts_native.get_native_tts_engine).

  - sev: P1
    id: P09-F04
    titre: TTSKit stdout PCM polluable + MIME web faux
    where: native_audio/ttskit_mlx.py:101-118 ; audio/audio_format.py:105-112
    detail: >
      load_model() sans redirection stdout ; tts_audio_mime(ttskit)→audio/mpeg
      alors que payload = PCM16 brut.

  - sev: P1
    id: P09-F05
    titre: Deadlock potentiel play_stream_from_async
    where: audio/audio_output.py:299-364
    detail: >
      Producer bloque sur Queue(16) ; si consumer échoue avant drain et sans
      _stop_flag, await producer hang.

  - sev: P1
    id: P09-F06
    titre: Échec initial ouverture micro non fatalisé
    where: scripts/audio_daemon.py:775-890, 543-555, 1471-1500
    detail: >
      Exception pa.open absorbée dans le thread ; watchdog ne compte pas
      _stream is None → daemon « error » mais vivant/muet.

  - sev: P2
    id: P09-F07
    titre: Émotions TTS sans effet acoustique
    where: audio/tts.py ; audio/tts_native.py ; native_audio/ttskit_mlx.py
    checklist: 5

  - sev: P2
    id: P09-F08
    titre: Stream Kokoro ONNX = multi-RIFF concaténé
    where: audio/tts.py:343-349
    detail: Chaque chunk = WAV complet ; client qui concatène casse le fichier.

  - sev: P2
    id: P09-F09
    titre: Exception moteur STT casse toute la chaîne de fallback
    where: audio/stt_daemon.py:459-470
    detail: try/except absent par backend ; seul result is None déclenche le repli.

  - sev: P2
    id: P09-F10
    titre: Conteneurs audio dépendent de faster-whisper même si WhisperKit/cpp actifs
    where: audio/stt_daemon.py:614-623

  - sev: P2
    id: P09-F11
    titre: afplay fallback ignore OUTPUT_DEVICE
    where: scripts/audio_daemon.py:1770-1788
    checklist: 4

  - sev: P2
    id: P09-F12
    titre: Cache TTS sans clé moteur / MIME défaut audio/mpeg
    where: audio/tts_cache.py
    detail: Risque rejeu Edge bytes ou MIME faux sur « répète ».

  - sev: P2
    id: P09-F13
    titre: VoiceQueue non bornée + requêtes timeout encore jouables
    where: audio/voice_queue.py:43-126
    checklist: 7

  - sev: P2
    id: P09-F14
    titre: STT model path confondu (STT_MODEL vs WHISPERCPP_MODEL_PATH)
    where: audio/stt_daemon.py ~553
    detail: large-v3-turbo passé comme chemin fichier si engine=whispercpp.

  - sev: P3
    id: P09-F15
    titre: Heuristique PCM tautologique
    where: audio/audio_format.py:56-60

  - sev: P3
    id: P09-F16
    titre: Fallback 1er micro peut être un loopback virtuel
    where: scripts/audio_daemon.py:1867-1871

LATENCE_OBSERVEE_CODE:
  - "Daemon cible <2s fin phrase→TTS (commentaire audio_daemon.py:1161-1162) — NON MESURÉ runtime"
  - "voice_debug_stt / voice_debug_tts events présents"
  - "Timeouts STT asyncio n'interrompent pas executor/subprocess sous-jacents"

THREAD_QUEUES_SYNTHESE:
  micro_queue: maxsize=300 (drop oldest ~3s)
  utterance_queue: maxsize=3 (drop new)
  voice_queue: heap illimité + priorités
  pcm_stream_queue: maxsize=16 (risque deadlock)
  stt_singleton: partage state sans _load_lock effectif

MATRICE_FORMATS:
  STT_in: [PCM16, WAV, WebM/Opus, MP3, OGG, M4A/AAC]
  TTS_out_kokoro_mlx: WAV PCM16 mono 24kHz
  TTS_out_kokoro_onnx_stream: WAV×N (fragile)
  TTS_out_macos_web: M4A ; native: WAV via afconvert
  TTS_out_edge: MP3
  TTS_out_ttskit: PCM16 brut (MIME annoncé incorrect hors daemon)
  playback: sounddevice(+afconvert) → afplay

TESTS_COUVERTURE_RESIDUELLE:
  presents: test_audio_daemon_device_resolve, test_kokoro_mlx, test_audio_output_routing (untracked)
  manquants:
    - pollution stdout sidecars
    - émotions acoustiques
    - WebM timeslice continuous_recorder
    - rejet whisper.cpp segments=[]
    - deadlock streamer
    - TTSKit→Edge get_tts_by_name

RECOMMANDATIONS_PRIORITAIRES:
  1. Concaténer blobs MediaRecorder avant STT (F01)
  2. Ne pas rejeter text non vide si segments absents (F02)
  3. get_tts_by_name(ttskit) → erreur/local fallback, jamais Edge (F03)
  4. Appliquer _silence_stdout à TTSKit + MIME pcm/wav correct (F04)
  5. Mapper émotions → rate/pitch/instruct ou retirer le contrat (F07)
  6. Fixer drain/_stop_flag sur échec consumer stream (F05)
```

**Verdict :** P09 est **CONDITIONAL_PASS** — invariant « pas de STT cloud » tenu ; Kokoro MLX WAV/stdout OK. Bloquants réels côté robustesse : WebM fragmenté, rejet whisper.cpp, fuite TTSKit→Edge web, émotions inertes, et risques thread/queue sur la sortie native.