# 30 — Plan de stabilisation audio après la PR #17

**Date initiale** : 15 juillet 2026
**Mise à jour d'état** : 27 août 2026
**Point de départ** : PR #17, pipeline natif macOS local  
**Règle** : une phase = une PR réversible, avec tests et preuve de validation

## Objectif

Traiter l'inventaire des erreurs audio sans réintroduire de fournisseur STT/TTS
abandonné. Le chemin cible est local pour le daemon : **faster-whisper `large-v3-turbo`** (STT) et
**Qwen3-TTS 0.6B Base 6-bit** (TTS) par défaut sur Apple Silicon. Les autres
moteurs restent des choix explicites : aucun changement de fournisseur ni repli
réseau ne peut être déclenché silencieusement.

Le statut et les mesures courantes de Qwen3 sont détaillés dans
`docs/audio/QWEN3_LOCAL_STATUS.md`. Les références Kokoro ci-dessous ont été
remplacées par le contrat réellement déployé ; elles ne décrivent plus la cible.

## Ordre des PR

| Phase | Branche proposée | Priorité | État au 10/08 | Résultat attendu |
|---|---|---:|---|---|
| 0 | `codex/phase-0-remove-legacy-audio` | Bloquante | Terminé | Ancien fournisseur supprimé du code, de la configuration, des tests et de l'UI |
| 1 | `codex/phase-1-local-stt-bootstrap` | P0 | Terminé | Un moteur STT local choisi, préchargé et diagnostiqué sans faux positifs |
| 2 | `codex/phase-2-native-stt-bridges` | P1 | Terminé | WhisperKit/whisper.cpp installables, chemins et modèles vérifiés |
| 3 | `codex/phase-3-audio-daemon-resilience` | P0 | Partiel | Plus d'abandon silencieux après crash ou micro muet ; preuves matérielles encore requises |
| 4 | `codex/phase-4-local-tts-resilience` | P1 | Terminé techniquement | Qwen3 local testé, erreurs actionnables, aucun repli silencieux |
| 5 | `codex/phase-5-voice-websockets` | P1 | Terminé techniquement | Poussoir, mains libres et temps réel partagent le même contrat STT |
| 6 | `codex/phase-6-recording-diarization` | P2 | Partiel | Capture longue reprenable livrée et testée virtuellement ; validation navigateur/STT réelle et diarisation locale restent ouvertes |
| 7 | `codex/phase-7-audio-observability` | P1 | Partiel | Dashboard et outil de campagne livrés ; campagne 24 h encore à exécuter |

## Phase 0 — Retrait du fournisseur legacy

**État** : terminée et couverte par les contrôles de non-régression.

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

- Tester la disponibilité de Qwen3, TTSKit et macOS sans charger plusieurs
  modèles simultanément.
- Formaliser l'ordre de repli et le format audio produit par chaque moteur.
- Refuser tout repli implicite entre fournisseurs ; chaque moteur est un choix
  de configuration observable.
- Corriger les échecs silencieux du sidecar, de `say`/`afconvert` et de
  l'énumération des voix.
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

**État technique au 27/08** : le vertical de capture longue ne concatène plus
des timeslices ambigus. L'UI canonique redémarre `MediaRecorder` toutes les 30 s
et produit ainsi des segments autonomes. Le protocole REST v1
`/api/recording-sessions` porte `sequence + SHA-256 + durée`, n'ACK qu'après
écriture atomique/fsync, reprend depuis `next_sequence`, déduplique un replay
identique et refuse un trou ou un replay différent. La file client garde au plus
deux blobs et chaque upload a trois essais transitoires maximum.

Le worker relit un seul fichier à la fois ; ni la reprise ni la transcription
segmentée ne reconstruisent l'audio complet en RAM. `complete` et le job
d'ingestion sont idempotents, `cancel` ne s'applique qu'à une capture ouverte et
détruit l'audio brut sous le verrou d'upload, et les dérivés
restent uniques par `recording_session_id`. La liste Documents consomme
désormais l'enveloppe API réelle et expose démarrage, reprise, état, annulation
et retry.

- Valider les segments MediaRecorder indépendants avant transcription.
- Introduire un moteur de diarisation local dans une PR distincte, ou conserver
  la fonction désactivée avec un statut explicite.
- Classer séparément les erreurs STT, extraction, synthèse et actions aval.
- Rendre les reprises idempotentes pour éviter les tâches et faits dupliqués.

**Preuve automatisée** : `tests/test_recording_upload_protocol.py` simule
1/30/180 minutes sans attente murale et couvre ACK perdu, reprise après crash,
replay, trou, mauvais checksum/conteneur, stockage plein, timeout STT,
annulation, retry borné et unicité du job. Les tests UI couvrent l'enveloppe
Documents et le contrat de retry/cursor.

**Toujours requis avant clôture** : capture MediaRecorder réelle de 180 minutes,
transcription locale réelle sur le Mac cible, mesure RSS/latence/disque et essai
de reprise après crash physique. Ces preuves sont `NOT_EXECUTED`; la campagne
24 h reste en Phase 7.

## Phase 7 — Observabilité et clôture

- Exposer disponibilité, moteur, latence, taux d'échec et profondeur des files.
- Dédupliquer les logs et réserver `CRITICAL` aux pertes de service effectives.
- Rejouer l'inventaire initial, documenter chaque erreur éliminée ou reclassée.
- Exécuter la suite complète backend/frontend et la campagne manuelle 24 h.

L'outil `tools/run_release_soak.py` produit l'artefact borné de cette campagne.
Sa livraison ne vaut pas preuve d'exécution : la phase reste ouverte tant que
les 24 h sur le Mac cible et les scénarios matériels ne sont pas archivés.

**Definition of Done globale** : aucune erreur critique non expliquée, aucun
fallback cloud STT, aucune configuration legacy, et rollback documenté par PR.
