# 30 — Plan de stabilisation audio après la PR #17

**Date** : 15 juillet 2026  
**Point de départ** : PR #17, pipeline natif macOS local  
**Règle** : une phase = une PR réversible, avec tests et preuve de validation

## Objectif

Traiter l'inventaire des erreurs audio sans réintroduire de fournisseur STT/TTS
abandonné. Le chemin cible est local pour le daemon : **faster-whisper `large-v3-turbo`** (STT) et
**Kokoro `af_nicole`** (TTS) par défaut. Edge reste disponible **uniquement** via
`TTS_ENGINE=edge` (choix explicite) — jamais comme repli silencieux de Kokoro.
WhisperKit, whisper.cpp, TTSKit et `say` restent des moteurs optionnels explicites.

## Ordre des PR

| Phase | Branche proposée | Priorité | Résultat attendu |
|---|---|---:|---|
| 0 | `codex/phase-0-remove-legacy-audio` | Bloquante | Ancien fournisseur supprimé du code, de la configuration, des tests et de l'UI |
| 1 | `codex/phase-1-local-stt-bootstrap` | P0 | Un moteur STT local choisi, préchargé et diagnostiqué sans faux positifs |
| 2 | `codex/phase-2-native-stt-bridges` | P1 | WhisperKit/whisper.cpp installables, chemins et modèles vérifiés |
| 3 | `codex/phase-3-audio-daemon-resilience` | P0 | Plus d'abandon silencieux après crash ou micro muet |
| 4 | `codex/phase-4-local-tts-resilience` | P1 | Chaîne TTS locale testée avec repli déterministe |
| 5 | `codex/phase-5-voice-websockets` | P1 | Poussoir, mains libres et temps réel partagent le même contrat STT |
| 6 | `codex/phase-6-recording-diarization` | P2 | Enregistrements longs fiables ; diarisation locale explicitement optionnelle |
| 7 | `codex/phase-7-audio-observability` | P1 | Erreurs classifiées, métriques utiles et validation finale |

## Phase 0 — Retrait du fournisseur legacy

**État** : implémentée dans le présent lot, validation en cours.

- Supprimer le client STT cloud et ses secrets de configuration.
- Retirer le backend TTS, les réglages API et les options frontend associés.
- Faire de `audio.stt_daemon` la façade STT partagée par le daemon et les
  WebSockets ; décoder localement WebM/Opus, WAV, MP3 et OGG.
- Désactiver par défaut la diarisation tant qu'aucun moteur local ne la fournit.
- Ajouter un contrôle de non-régression : aucune référence au fournisseur retiré
  dans l'arbre de travail suivi par Git.

**Validation** : tests STT/TTS/API ciblés, typecheck frontend, `compileall`,
`git diff --check`.  
**Rollback** : revert unique de la PR de Phase 0.

## Phase 1 — Bootstrap STT local

- Séparer « moteur configuré » de « moteur réellement prêt » dans les statuts.
- Vérifier au démarrage dépendance, modèle, mémoire disponible et autorisation de
  téléchargement ; ne jamais télécharger pendant une conversation.
- Ne journaliser en erreur que l'échec du moteur sélectionné. Les moteurs
  optionnels absents restent des diagnostics, pas des incidents.
- Ajouter un endpoint de diagnostic local avec moteur actif, modèle et durée du
  dernier préchargement, sans chemin sensible.

**Sortie** : démarrage déterministe et tests pour moteur absent, modèle absent,
configuration invalide et préchargement réussi.

## Phase 2 — Bridges STT natifs

- Fournir une procédure reproductible de compilation du sidecar WhisperKit.
- Vérifier signature, droits d'exécution, version et sortie JSON du sidecar.
- Valider le binaire et le modèle whisper.cpp avant la première transcription.
- Supprimer le doublon de journalisation sur les retours non nuls du sidecar.

**Sortie** : tests contractuels avec faux binaires et test manuel Apple Silicon.

## Phase 3 — Résilience du daemon audio

- Ajouter un préflight explicite de permission micro macOS et exposer l'état à
  l'API au lieu d'attendre le watchdog.
- Distinguer silence réel, périphérique déconnecté et permission refusée.
- Remplacer l'abandon fixe de cinq minutes par un circuit breaker observable,
  réarmable et borné.
- Borner les files VAD/STT et conserver la cause racine des crashes consécutifs.

**Sortie** : tests de déconnexion/reconnexion, saturation et crash loop ; essai
manuel micro refusé puis réautorisé ; observation 24 h avant clôture.

## Phase 4 — Résilience TTS locale

- Tester la disponibilité de TTSKit, Kokoro et macOS sans charger plusieurs
  modèles simultanément.
- Formaliser l'ordre de repli et le format audio produit par chaque moteur.
- Corriger les échecs silencieux `say`/`afconvert` et l'énumération des voix.
- Ajouter des messages d'installation actionnables pour les composants choisis.

**Sortie** : matrice de tests moteur principal/repli/aucun moteur et lecture d'un
échantillon sur le Mac cible.

## Phase 5 — WebSockets vocaux

- Unifier poussoir, conversation, mains libres et mode temps réel sur une seule
  interface de transcription et une seule taxonomie d'erreurs.
- Tester les conteneurs MediaRecorder réels, les blobs tronqués, les timeouts et
  l'annulation client.
- Garantir le retour à l'état `listening` après chaque échec.

**Sortie** : contrats WebSocket automatisés et validation navigateur desktop/mobile.

## Phase 6 — Enregistrements longs et diarisation

- Valider la concaténation des fragments MediaRecorder avant transcription.
- Introduire un moteur de diarisation local dans une PR distincte, ou conserver
  la fonction désactivée avec un statut explicite.
- Classer séparément les erreurs STT, extraction, synthèse et actions aval.
- Rendre les reprises idempotentes pour éviter les tâches et faits dupliqués.

**Sortie** : tests 1/30/180 minutes, reprise après erreur et preuve d'absence de
doublons.

## Phase 7 — Observabilité et clôture

- Exposer disponibilité, moteur, latence, taux d'échec et profondeur des files.
- Dédupliquer les logs et réserver `CRITICAL` aux pertes de service effectives.
- Rejouer l'inventaire initial, documenter chaque erreur éliminée ou reclassée.
- Exécuter la suite complète backend/frontend et la campagne manuelle 24 h.

**Definition of Done globale** : aucune erreur critique non expliquée, aucun
fallback cloud STT, aucune configuration legacy, et rollback documenté par PR.
