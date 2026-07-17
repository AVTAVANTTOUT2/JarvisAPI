---
id: voice_pipeline
version: 2.0.0
date: 2026-07-16
domain: voice
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Pipeline vocal

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Pipeline vocal JARVIS, sensible à la latence de bout en bout : `api/voice_processing.py` (`_process_voice_fast`, réponses immédiates DeepSeek Flash) ; `api/voice_cognitive.py` (préambule cognitif : routage cursor / briefing / heavy, ack vocal immédiat puis suivi en arrière-plan) ; `audio/stt_daemon.py` (façade STT locale multi-moteurs) ; `audio/tts.py` (TTS Edge/local, streaming). Chaque échange trace ses latences segment par segment dans la table `voice_debug_log` (STT, route, LLM pass1/pass2, TTS, total).

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Point de départ obligatoire : les traces `voice_debug_log` (lecture via `database.get_voice_debug_logs()` ou export `scripts/export_voice_debug.py`) — identifie le segment fautif (STT ? routage ? LLM ? TTS ?) avant de toucher au code.
- Reproduis sur le chemin réel : un texte injecté dans le pipeline (sans micro) suffit pour la logique ; ne conclus rien sur la latence sans mesure tracée.
- Vérifie l'architecture avant modification : il existe UN pipeline vocal (préambule cognitif → `_process_voice_fast` → TTS) ; toute divergence constatée entre `api/mobile_voice_service.py`, `api/ws_handsfree.py` et le pipeline principal doit être signalée, pas aggravée.

# Périmètre
- Modifications à l'intérieur du pipeline existant : préambule dans `api/voice_cognitive.py`, réponse rapide dans `api/voice_processing.py`, moteurs dans `audio/`.
- Toute nouvelle étape DOIT tracer sa latence dans `voice_debug_log` (compléter `build_voice_debug_trace` si un champ manque).
- Préserver l'anti-écho : binaire ignoré tant que `is_speaking`/`is_processing`, buffers micro vidés quand JARVIS parle — tout changement du cycle écoute/parole doit maintenir ces invariants.

# Hors périmètre
- INTERDIT : ajouter un 2e pipeline vocal parallèle (nouveau chemin STT→LLM→TTS) — étendre l'existant.
- Repli cloud pour le STT : la chaîne STT reste locale (faster-whisper / WhisperKit / whisper.cpp).
- Dégrader la latence des réponses courtes (le mode vocal utilise `VOICE_MAX_TOKENS` et DeepSeek Flash — ne pas router les réponses immédiates vers le modèle lourd).

# Fichiers probables
{{context_files}}
- `api/voice_processing.py` (_process_voice_fast), `api/voice_cognitive.py` (préambule cursor/briefing/heavy), `api/ws_handsfree.py` (session mains libres), `api/mobile_voice_service.py` (chemin mobile), `audio/stt_daemon.py`, `audio/tts.py`, `audio/vad_*.py` ; traces : `database/devops.py` (voice_debug_log).

# Règles d'architecture
{{repo_rules}}
- Le routage cognitif (`jarvis/cognitive/router.py`) décide Flash/Main/Cursor ; le pipeline vocal ne court-circuite pas ce routage.

# Critères d'acceptation
{{acceptance_criteria}}
- Les latences tracées restent au niveau d'avant (ou s'améliorent) sur le chemin des réponses courtes.
- L'anti-écho et le cycle listening → processing → speaking → listening restent intacts.

# Tests obligatoires
{{required_tests}}
- Tests du routage vocal (`tests/test_cognitive_routing.py`) verts ; tests unitaires de la logique ajoutée (détection, découpage, formats audio) sans I/O réel.

# Validation réelle
- Exécuter un échange vocal simulé de bout en bout (texte → pipeline → réponse + trace) et joindre la trace `voice_debug_log` produite au rapport, avec les latences segment par segment.
- Comparer une trace AVANT et une trace APRÈS sur la même entrée.
- Vérifier que `speech_done` clôture toujours correctement le flux TTS streaming côté client.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, isolant logique de routage, moteurs audio et traces.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des latences (segment tracé), jamais masquer par un timeout ou un cache douteux.
- Ne pas masquer le problème ; ne rien inventer (chaque chiffre de latence vient d'une trace réelle).
- Ne pas supprimer une fonction existante sans preuve (les chemins voix mobile/mains libres partagent des helpers).
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après (traces) ; préserver les contrats existants (messages WS, format des chunks TTS).
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (trace de bout en bout jointe).
