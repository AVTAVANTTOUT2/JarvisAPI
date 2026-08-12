# ADR-028 — Politique de parole vocale : un énoncé par tour, un honorifique par session

- **Statut** : accepté
- **Date** : 2026-08-12
- **Portée** : daemon audio local, page `/voice`, mains-libres WebSocket, mobile,
  cache TTS, prompts vocaux

## Contexte

Un tour vocal normal produisait deux prises de parole :

```
Utilisateur : Quel temps fait-il à Lille ?
JARVIS      : Bien, Monsieur.
JARVIS      : Il fait 18 degrés à Lille, Monsieur.
```

Deux producteurs distincts s'empilaient, et aucun ne connaissait l'autre.

**Le premier** était `_play_anticipatory_ack()` dans `scripts/audio_daemon.py` :
une phrase fixe, jouée pendant que le moteur canonique préparait la réponse,
passée à `_process_voice_fast` par `on_canonical_turn_started`. Le tour attendait
ensuite les deux tâches via `asyncio.gather()`.

**Le second** était la persona. `prompts/persona.txt`, injecté dans tous les
agents user-facing, apprenait au modèle à dire « Monsieur » — avec pour exemple
canonique « Bonjour Monsieur. Que puis-je faire pour vous ? ». La directive
vocale ajoutée dans `agents/__init__.py` demandait de la concision mais ne
disait rien des salutations ni de l'honorifique.

S'y ajoutaient une dizaine de producteurs écrivant l'honorifique en dur :
réponses de contrôle de `api/voice_fastpath.py`, replis d'action de
`api/voice_support.py` (vingt-cinq occurrences), accusés Cursor, service mobile,
et surtout `CANNED_PHRASES` dans `audio/tts_cache.py`, qui **pré-synthétisait**
« Bien, Monsieur. ».

### Trois défauts, pas un seul

1. **Une prise de parole artificielle** devant presque chaque réponse normale,
   suivie d'une seconde qui répétait l'honorifique.
2. **De la latence ajoutée.** Le tour n'attendait pas la génération de l'accusé
   mais sa **lecture** : la vraie réponse pouvait être prête et rester en file.
3. **Un micro qui se fermait.** En semi-duplex — le défaut — l'accusé arrêtait
   le flux d'entrée. Seule la fin de tour normale le rouvrait ; les sorties
   anticipées passaient par `_rearm`, qui ne le faisait pas. Un repli qualité
   STT déclenché sur du bruit suffisait à rendre le daemon sourd.

Le point 3 est le plus révélateur : l'accusé n'était pas seulement inélégant,
il était couplé à l'état matériel du micro.

## Décision

### 1. L'accusé anticipé générique est supprimé, pas désactivé

`_play_anticipatory_ack()` devient `_signal_processing_started()` : un
changement d'état diffusé sous `voice_processing_started`, sans TTS, sans
manipulation du flux d'entrée, sans attente. `_process_voice_fast` ne fait plus
de `gather` — le signal part en parallèle et est récolté à la fin pour ne
laisser aucune tâche orpheline.

Le repli qualité STT reçoit le même traitement : `voice_quality_fallback`,
observable et muet.

`VOICE_ANTICIPATORY_ACK_ENABLED` rejoint `config.RETIRED_ENV_VARS`. Un `.env`
qui la définit encore obtient un avertissement, pas un silence trompeur.
Conserver le drapeau aurait conservé le code mort qui va avec, et donc la
possibilité de rallumer un comportement que nous jugeons défectueux.

### 2. La progression d'un travail long est un objet différent

Un accusé parlé reste légitime — « Je lance l'analyse. » — mais seulement
lorsqu'un job a **réellement** été créé et accepté, jamais pour masquer le temps
de premier jeton d'un LLM. Réglé par `VOICE_PROGRESS_ACK_POLICY`
(`long_jobs_only` par défaut, `never` possible). Il est émis par le producteur
du travail, pas par le pipeline vocal.

### 3. Une politique d'adresse centrale et déterministe

`jarvis/voice/address.py` porte `VoiceUtteranceKind`, `VoiceUtterance`,
`VoiceSession` et `VoiceAddressPolicy`. La règle dépend du **type d'énoncé**,
pas de son texte : « Bonjour Monsieur. » est correct à l'ouverture d'une session
et incorrect au milieu, et seul l'appelant connaît la différence.

| Type | Honorifique |
|---|---|
| `ANSWER`, `ACTION_CONFIRMATION`, `PROGRESS`, `ERROR`, `SYSTEM_SIGNAL` | interdit |
| `GREETING`, `FAREWELL`, `RITUAL` | une fois au maximum par session |

Modes : `rare` (défaut), `never`, `free`.

Deux goulets d'application, qui couvrent tous les transports :
`api/voice_processing._speakable()` pour tout tour (daemon, `/voice`,
mains-libres, mobile), et `AudioDaemon._play_tts()` pour ce que le daemon dit de
sa propre initiative (notifications, veille, pannes).

Une frontière de session est celle de la conversation, **pas** une détection de
wake word : réveiller JARVIS trois fois pour trois questions successives reste
la même session, et ne rouvre donc pas le droit de saluer.

### 4. Le prompt n'est pas la seule garantie

`persona.txt`, `VOICE_ADDRESS_OVERLAY` et la directive vocale de
`agents/__init__.py` énoncent la règle. Le filtre déterministe la garantit. Les
deux sont nécessaires : le prompt seul échoue parfois, et il ne touche
absolument pas les producteurs qui ne sont pas des modèles.

Le filtre ne fait **jamais** de remplacement global. Sont laissés intacts :
citations et titres entre guillemets, civilité d'un tiers (« Monsieur Dupont »),
emploi comme nom commun (« ce monsieur »), position de sujet (« Monsieur a couru
toute la journée »). L'espace typographique française avant `? ! ; :` est
préservée — le TTS s'en sert pour la prosodie.

### 5. Le cache TTS entre dans la politique

Un audio pré-généré court-circuite la synthèse, donc aussi le filtre appliqué au
texte. Deux mesures : la version de politique entre dans la clé de cache, et
`SpeculativeTTS` refuse d'entrer comme de sortir toute phrase que la politique
réécrirait. Sans cela, une session déjà chaude aurait continué de **jouer**
« Bien, Monsieur. » alors que la génération, elle, était corrigée.

### 6. « stop » ne répond rien

Une commande d'arrêt coupe la lecture et rend la main. Répondre « Bien. » à une
demande de silence exécute l'ordre et le contredit dans le même souffle. Le
fast-path rend `SILENT_ACKNOWLEDGEMENT` (chaîne vide), distinct d'une absence de
correspondance, et aucun tour d'assistant vide n'est écrit dans l'historique.

## Conséquences

**Positives.** Un tour normal produit exactement un énoncé. Le chemin critique
perd l'attente de lecture d'une phrase creuse. Le couplage entre accusé et état
du micro disparaît. La personnalité est préservée là où elle a du sens et un
test statique empêche la dérive des chaînes en dur.

**Négatives assumées.** Le filtre déterministe est un traitement de texte par
expressions régulières : il couvre les emplois attendus en français, pas tous
les emplois concevables. Il est idempotent et testé sur les cas de protection,
mais un tour de phrase inhabituel pourrait lui échapper — dans un sens ou dans
l'autre. `VOICE_ADDRESS_POLICY=free` le désactive intégralement.

Le mode `free` est une porte de sortie, pas une seconde implémentation : aucun
chemin parallèle n'est maintenu.

## Ce que ce lot ne fait pas

Cette décision porte sur **ce que JARVIS dit**. Elle ne porte pas sur la
mécanique de latence du pipeline, qui reste séquentielle après la fin de parole
(STT complet → LLM → TTS). Ne sont donc **pas** couverts, et restent ouverts :

- STT incrémental et hypothèses partielles ;
- endpointing adaptatif (le silence fixe reste en place) ;
- annulation d'écho (AEC) et fonctionnement plein duplex ;
- barge-in généralisé et suivi de la portion réellement prononcée
  (`generated_text` / `queued_text` / `spoken_text`) ;
- séparation formelle `turn_id` / `speech_id` / `job_id` au-delà des champs
  déclarés dans `VoiceUtterance`, qui ne sont pas encore alimentés de bout en
  bout ;
- streaming LLM → TTS incrémental (le streaming actuel sert à mesurer
  `llm.first_token`, pas à parler plus tôt — un bloc `action` peut remplacer le
  texte) ;
- banc de mesure de latence avant/après.

Ces chantiers touchent l'audio natif CoreAudio et le moteur STT ; les mêler à
une correction de comportement produisait une revue impossible à mener.

## Retour arrière

`VOICE_ADDRESS_POLICY=free` restaure la parole non filtrée sans redémarrage de
code. L'accusé anticipé, lui, ne revient que par `git revert` : c'est
intentionnel — le drapeau maintenait le défaut en vie.
