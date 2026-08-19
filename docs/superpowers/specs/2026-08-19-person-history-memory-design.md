# Mémoire relationnelle — chapitres mensuels et synthèse

Date: 2026-08-19  
Statut: proposé (en attente de validation)

## Objectif

Quand tu demandes *qui est Bertille* ou *ce qui s’est passé dans notre histoire*, JARVIS répond à partir d’une **mémoire déjà distillée**, pas d’une recherche mot-clé sur 40 000 messages. S’il lui manque un morceau, il **le construit** (job d’ingestion), il ne prétend pas que l’index est vide et il n’envoie pas OpenCode fouiller SQLite.

## Le vrai trou (constaté le 19 août)

Les iMessages sont dans `jarvis.db` (plusieurs milliers de messages pour ce contact, ingestion `complete`). Ce qui manque :

1. **Routage.** « qui est X » n’extrait pas le nom. La recherche globale fait gagner les *chats JARVIS* titrés avec le prénom, pas le fil iMessage.
2. **Dossier vide.** `relationship_events` / faits / `ai_description` sont à zéro. Le sync Mac a posé `imessage_analysis_cache.last_analyzed_rowid` comme si l’extracteur LLM avait tourné ; le job quotidien ne relit plus rien.
3. **Pas de couche « histoire ».** `relationship_events` est une liste d’atomes, pas un récit mois par mois. `ContactAnalytics` a des stats, pas de narration.

Le merge iMessage (miroir `chat.db` → `jarvis.db`) ne change rien à ces trois points.

## Ce que tu as proposé, et ce que je change

Ton idée (une table de résumés mensuels + citations, puis synthèse à la question) est la bonne couche. Je la serre en quatre règles :

| Ta proposition | Perfectionnement |
|---|---|
| 1 table = 1 mois × 1 personne | Oui, **chapitre** versionné, pas un dump. Unique `(person_id, year_month)`. |
| Citations des moments forts | Identifiants `apple_rowid` + extraits **courts** bornés. Jamais le mois entier dans le LLM au moment de la question. |
| « Select tous les résumés puis synthétise » | Oui pour une question d’*histoire*. Pas pour « qu’est-ce qu’elle m’a dit hier » (là, messages bruts de la fenêtre). |
| OpenCode + plusieurs agents qui SQL la base | **Non comme chemin principal.** OpenCode = code / git / CI en worktree. La mémoire personnelle passe par retrieval + jobs d’ingestion. Un run agentique *peut* lire les chapitres déjà indexés via le MCP knowledge (lecture seule), il n’écrit pas dans SQLite. |

JARVIS « se débrouille » = **détecter le trou, enfiler un job, répondre avec ce qui existe, prévenir quand le reste est prêt**. Pas lancer un coding agent sur `data/jarvis.db`.

## Trois types de questions (déterministes)

Le routeur doit classer **avant** tout LLM :

| Type | Exemples | Source |
|---|---|---|
| Identité | qui est X, c’est qui X | dossier (`people` + profil) + 3 derniers chapitres |
| Histoire | notre histoire, ce qui s’est passé avec X, depuis le début | **tous** les chapitres de X, puis une synthèse Main |
| Fait récent | ce qu’elle m’a écrit hier / ce week-end | `imessage_messages` bornés par dates (`time_buckets`), pas les chapitres |

Si le type est *histoire* et qu’il manque des mois : répondre sur les chapitres présents + dire clairement ce qui est en cours de construction. Interdit : « index partiel » alors que `ingestion_source_state.imessage.completeness = complete`.

## Architecture — 4 couches

```
imessage_messages          (vérité brute, déjà miroir)
        │
        ▼  job ingestion (pas le tour de chat)
person_month_chapters      (1 ligne / personne / mois civil TIMEZONE)
        │
        ▼  après chaque chapitre écrit
people.ai_description      (dossier court, 800–1500 car.)
relationship_profile       (style, sujets — déjà là, aujourd’hui vide)
relationship_events        (moments forts extraits du chapitre, pas un 2e récit)
        │
        ▼  question
retrieval (source person_month) + synthèse bornée
        │
        ▼  trou
knowledge job / job d’ingestion « chapter missing months »
        │
        ▼  seulement si la demande est une tâche logicielle
runtime agentique + OpenCode (worktree, plan approuvé)
```

Une seule écriture propriétaire : le LaunchAgent `com.jarvis.ingestion` (identité TCC). Le chat ne fait que lire et **enfiler**.

## Table `person_month_chapters`

Pas une copie de `weekly_summaries` (global) ni de `relationship_events` (atome).

```text
id
person_id              FK people ON DELETE CASCADE
year_month             TEXT 'YYYY-MM' dans TIMEZONE
period_start_utc       TEXT
period_end_utc         TEXT
status                 empty | partial | complete
message_count          INTEGER   (déterministe)
sent_count / recv_count
highlights_json        JSON [{apple_rowid, occurred_at_utc, quote, kind}]
narrative              TEXT      (récit du mois, borné ~2000 car.)
mood_arc               TEXT      (optionnel, court)
source_rowid_min/max   INTEGER   (reprise / invalidation)
content_hash           TEXT      (messages du mois ; si identique → skip LLM)
model                  TEXT
tokens_in / tokens_out / cost
created_at / updated_at
UNIQUE(person_id, year_month)
```

`kind` ∈ `turning_point | conflict | plan | absence | affection | logistics` — vocabulaire fermé, pas de prose libre dans le JSON.

Invariants :

- Le chapitre se calcule **uniquement** depuis `imessage_messages` déjà importés. Jamais d’ouverture de `chat.db` ici.
- Citations : extraits ≤ 200 caractères, déjà dans le miroir. Pas de PII supplémentaire inventée.
- `empty` si 0 message ce mois (on **stocke** la ligne : « pas de contact en mars » est une information).
- Recalcul si `content_hash` change (messages rattrapés par le reconcile iMessage).

## Job `person_history` (ingestion)

Horaire : tous les jours vers 03:30, après le sync iMessage, **borné**.

Priorité, dans l’ordre :

1. Personnes mentionnées dans les 7 derniers jours de chat JARVIS (la question d’aujourd’hui).
2. Top N par `people.imessage_count` (N configurable, défaut 15).
3. Mois calendaire **écoulé** d’abord (août se ferme le 1er septembre), plus le mois courant en `partial` si ≥ 20 nouveaux messages depuis le dernier hash.

Plafonds : `PERSON_HISTORY_MAX_CHAPTERS_PER_RUN` (défaut 8), `PERSON_HISTORY_MAX_MESSAGES_PER_CHAPTER` (défaut 400, échantillonnage déterministe si plus : plus longs, avec PJ, après un trou > 48 h, plus `ContactAnalytics` en préface **sans LLM**).

Un appel modèle **par chapitre**, modèle rapide, JSON strict, puis `narrative`. Échec → `partial` + retry, curseur d’extracteur **inchangé** (le contraire du bug Mac sync).

Après un chapitre `complete` : mettre à jour le dossier (`ai_description`) à partir des **12 derniers** chapitres, un seul appel. Émettre `person.chapter_updated` sur le bus (SSE existant).

## Curseur d’analyse — couper le mensonge

Aujourd’hui `force_upsert_people_from_mac_sync` écrit `last_analyzed_rowid = max(ROWID)` et `total_messages_analyzed = msg_count` **sans LLM**. L’analyseur quotidien croit le travail fait.

Séparer :

- `imessage_sync_cursor` / comptes people = **import**
- `imessage_analysis_cache.last_extracted_rowid` = **dernier batch réellement passé à l’extracteur**

Le sync Mac n’a plus le droit de avancer `last_extracted_rowid`. Sans ça, les chapitres se remplissent mais l’extracteur historique reste mort.

## Question — retrieval, pas un second pipeline

1. Extraire le nom : `qui est X`, `c’est qui X`, `histoire avec X`, `ce qui s’est passé avec X` (aujourd’hui seul « messages avec X » marche).
2. Forcer `RetrievalRequest.person` + sources `person`, `relationship`, `relationship_event`, `person_month`, et iMessage **seulement** pour le type *fait récent*.
3. Plafond : 12 chapitres + 1 dossier. Synthèse Main si type *histoire* ; Flash si *identité*.
4. Interdit de laisser les titres de `conversations` JARVIS évincer le dossier (boost `person_filter` / source `person_month`).
5. Adapter retrieval `person_month` → index knowledge (trigger comme les autres tables).

Le coach / `build_full_context()` n’injecte **pas** 200 chapitres. Il injecte, si une personne est résolue dans le tour : dossier + liste `year_month + status` (pas les narratifs ; ceux-là viennent du retrieval du tour).

## Autonomie : trou → job, pas OpenCode

```
question histoire
  → chapitres couvrant la période ? 
       oui → synthétiser
       non → enqueue job (person_id, year_months manquants)
            → répondre avec l’existant
            → « Je construis les mois M… ; je te préviens. »
            → event person.chapter_updated → notification / phrase courte
```

C’est le même patron que `knowledge_index_jobs` : idempotent, lease, retry. **Pas** une tâche agentique par défaut : pas de worktree, pas d’approbation de plan, pas de modèle coding. Une question personnelle ne doit pas ouvrir un checkout Git.

## Où l’agentique / OpenCode a sa place

| Demande | Chemin |
|---|---|
| Histoire / qui est | Chapitres + retrieval (ci-dessus) |
| « Analyse Bertille » / dossier périmé | Job `person_history` immédiat (comme aujourd’hui `POST /api/analyze-contact`, étendu) |
| « Prépare un cadeau / un message, tu connais l’histoire » | Chat normal **avec** chapitres dans le snapshot de tour. Si l’utilisateur veut une *production* (brouillon long, fichier), **là** task-control + plan à valider. OpenCode peut *lire* les UID knowledge du tour (MCP déjà read-only), pas SQL libre. |
| « Le job de chapitres est cassé, corrige le code » | Runtime agentique + OpenCode, plan approuvé, worktree. C’est de l’ingénierie. |

Plusieurs agents qui « cherchent dans la base » dupliqueraient `search_knowledge` en plus dangereux (PII dans un worktree). On ne le fait pas.

## API / UI (minimal)

- `GET /api/people/{name}/history?from=2024-01&to=2026-08` → chapitres (auth session).
- `POST /api/people/{name}/history/rebuild` → enfile les mois manquants (CSRF). Pas de LLM dans la requête HTTP.
- Fiche contact existante : section « Histoire » liste les mois. Pas de nouvelle app.

Voix : identité = 3 phrases du dossier. Histoire = « trop long à l’oral, j’affiche / je résume les trois derniers mois, le reste est dans l’app ».

## Sécurité

- Même verrou de session que `/api/people`.
- Chapitres = données privées, `sensitivity=private`, `cloud_policy=redact` côté knowledge (les extraits vont au LLM comme le reste iMessage, PII pass-through déjà décidé).
- Logs : pas de citations, pas de numéros.
- Plafond coût : skip si `PERSON_HISTORY_DAILY_TOKEN_BUDGET` atteint.
- Pas d’écriture `chat.db`.

## Tests (sans corps de messages réels)

- Extraction de nom : `qui est Ada`, `c’est qui Ada`, `histoire avec Ada`.
- Scoring : un chapitre `person_month` bat un titre de conversation JARVIS.
- Job : 3 messages en janvier → 1 chapitre `complete` ; 0 message en février → ligne `empty` ; même hash → 0 appel LLM.
- Sync Mac ne bouge plus `last_extracted_rowid`.
- Trou : question histoire sans janvier → job enqueued + réponse sans prétendre l’absence de traces.
- Scan statique : aucun chemin OpenCode / agentic n’importe `person_month_chapters` en écriture.

## Hors scope (volontaire)

- Diarisation / attribution automatique d’identité réelle.
- Fusion magique de deux fiches people distinctes.
- Résumés par *semaine* (trop cher, trop bruyant).
- Envoi iMessage.
- Faire d’OpenCode un client SQL.

## Critère d’acceptation produit

Sur un contact avec > 1000 messages déjà importés :

1. Après backfill prioritaire, `GET .../history` liste des mois `complete` ou `empty`, pas un trou silencieux.
2. « Qui est X » cite le dossier, **pas** uniquement un vieux chat JARVIS.
3. « Notre histoire » synthétise plusieurs chapitres avec au moins une citation datée.
4. Un mois manquant déclenche un job, jamais la phrase « index partiel » si iMessage est `complete`.
