# Plan d’implémentation — mémoire historique par personne

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JARVIS sait qui est une personne et ce qui s’est passé avec elle, sans dump iMessage dans le tour de parole, sans OpenCode sur SQLite, et avec une autonomie d’ingestion (job + file) plutôt qu’une tâche agentique par question.

**Architecture:** Vérité brute dans `imessage_messages`. Un job d’ingestion distille un chapitre par personne et par mois civil (`person_month_chapters`). Le tour de chat classe la question (identité / histoire / fait récent) avant tout LLM, lit les chapitres, et enfile un job s’il manque un mois. OpenCode n’entre que pour de l’ingénierie.

**Tech Stack:** SQLite (`schema.py` + migration), FastAPI (`router_people.py`), retrieval (`jarvis/retrieval/coordinator.py`), ingestion (`scripts/ingestion_worker.py` + job dédié), event bus (`person.chapter_updated`).

## Global Constraints

- Spec : `docs/superpowers/specs/2026-08-19-person-history-memory-design.md`
- Jamais ouvrir `~/Library/Messages/chat.db` depuis ce job (seul `AppleDataService` le fait, ailleurs)
- Aucun log de citations, numéros, PIN, jetons, corps de messages
- Unique `(person_id, year_month)` ; `kind` ∈ `turning_point | conflict | plan | absence | affection | logistics`
- Citations `quote` ≤ 200 caractères ; narrative ≤ ~2000 caractères
- Plafonds : `PERSON_HISTORY_MAX_CHAPTERS_PER_RUN=8`, `PERSON_HISTORY_MAX_MESSAGES_PER_CHAPTER=400`, budget tokens journalier
- Chapitre : **modèle rapide** + JSON strict. Synthèse d’histoire au moment de la question : **modèle principal**. Identité vocale : 3 phrases
- Écriture SQLite : uniquement LaunchAgent `com.jarvis.ingestion`. Le chat enfile, il n’écrit pas
- OpenCode / runtime agentique : **interdit** d’écrire `person_month_chapters` ; autorisé seulement pour corriger le code (plan task-control)
- Tests : fixtures fictives (`Ada`, `AliceTest`), jamais `chat.db` réel
- Ne pas merger avec la PR iMessage fidélité (#265)

---

## File map (à créer / toucher)

| Fichier | Rôle |
|---------|------|
| `database/schema.py` | Table `person_month_chapters` |
| `database/migrations.py` + `database/migrations/*.sql` | Migration idempotente |
| `database/person_history.py` | CRUD chapitres, digest, rebuild enqueue |
| `database/people.py` | Ne plus poser `last_analyzed_rowid` comme extraction |
| `scripts/person_history.py` | Job mensuel |
| `scripts/ingestion_worker.py` | Dispatcher `person_history` |
| `database/ingestion.py` | Type de job |
| `jarvis/retrieval/coordinator.py` | Personne extraite + ranking |
| `api/chat_context.py` | Contexte identité / histoire |
| `jarvis/cognitive/router.py` | Patterns contact |
| `api/router_people.py` | GET history, POST rebuild |
| `jarvis/events.py` | Type `person.chapter_updated` |
| `tests/test_person_history.py` | Contrats (pas de PII réelle) |
| `tests/test_retrieval_coordinator.py` | Extraction + ranking |
| `tests/test_imessage_import.py` | Curseur Mac sync (si déjà couvert ailleurs, étendre people tests) |

---

### Task 1: Lot 0 — retrieval « qui est X » (sans table chapitres)

**Files:**
- Modify: `jarvis/retrieval/coordinator.py`
- Modify: `jarvis/cognitive/router.py`
- Modify: `tests/test_retrieval_coordinator.py` (ou nouveau fichier si trop gros)
- Test: `tests/test_cognitive_routing.py` si les patterns y sont verrouillés

- [ ] **Step 1: Extraire le nom sur les questions d’identité**

`_extract_structured_person` accepte au minimum :

```
qui est {Name}
c'est qui {Name}
c est qui {Name}
histoire (avec|de) {Name}
ce qui s'est passé avec {Name}
messages? (avec|de) {Name}
```

`Name` : 2–40 chars, pas de ponctuation de phrase. Un seul nom capturé.

- [ ] **Step 2: Ranking**

Quand un nom structuré est extrait :

1. Forcer un hit `person` (fiche) même si le score lexical du titre de conversation JARVIS est plus haut.
2. Inclure `relationship` / events liés.
3. **Ne pas** ajouter 8 hits iMessage bruts pour une question d’identité.

Quand la question est *histoire* (mots-clés ci-dessus) : même chose + plus tard `person_month` (Task 4).

- [ ] **Step 3: Router cognitif**

Ajouter les mêmes motifs à `_CONTACT_PATTERNS` (ordre : avant les motifs trop larges).

- [ ] **Step 4: Tests**

```python
assert extract("qui est Bertille") == "Bertille"
assert extract("c'est qui Bertille") == "Bertille"
hits = search_knowledge("qui est Bertille")
assert any(h.source_type == "person" for h in hits)
assert not any(h.source_type == "imessage" for h in hits[:8])  # identité
```

Données de test : fixtures SQLite, **pas** le prénom réel obligatoire ; un nom fictif `AliceTest` suffit.

- [ ] **Step 5: Commit**

```
fix(retrieval): extraire le nom sur « qui est X » et prioriser la fiche
```

---

### Task 2: Corriger le mensonge de curseur Mac sync

**Files:**
- Modify: `database/people.py` (`force_upsert_people_from_mac_sync`)
- Modify: tests people / mac sync existants

- [ ] **Step 1:** `imessage_analysis_cache` : poser `last_analyzed_rowid` **uniquement** comme curseur d’**import/sync** si c’est encore le contrat documenté ; **ne plus** laisser l’extracteur quotidien croire que 5095 messages ont été lus par Haiku.

Recommandé : colonne `last_extracted_rowid` (défaut 0) + laisser `last_analyzed_rowid` = max rowid **vu** (inventaire). L’extracteur lit `last_extracted_rowid`.

Si migration trop lourde pour ce lot : `UPDATE imessage_analysis_cache SET last_analyzed_rowid = 0` n’est **pas** acceptable (perdrait le max vu). Préférer la nouvelle colonne.

- [ ] **Step 2:** `relationship_analyzer` / `ContactAnalytics` : consommer `last_extracted_rowid`.

- [ ] **Step 3:** Tests : après `force_upsert_people_from_mac_sync`, `last_extracted_rowid` reste 0.

- [ ] **Step 4: Commit**

```
fix(people): ne plus marquer l’extraction relationnelle comme faite au sync Mac
```

---

### Task 3: Table `person_month_chapters` + CRUD

**Files:**
- Modify: `database/schema.py`
- Modify: `database/migrations.py` + nouveau SQL
- Create: `database/person_history.py`
- Modify: `tests/test_phase3_schema.py` / contrat schéma si existant

- [ ] **Step 1:** Table exactement comme la spec (UNIQUE person_id + year_month, CHECK status, highlights_json, content_hash, bornes UTC).

- [ ] **Step 2:** Helpers :

- `get_chapters(person_id) -> list[dict]` (ordre chronologique)
- `upsert_chapter(...)` (idempotent sur hash)
- `digest_for_identity(person_id) -> str` (3 derniers chapitres, plafonné)
- `digest_for_history(person_id) -> str` (tous, plafonné ~8k chars)

Aucun log de narrative.

- [ ] **Step 3:** Tests schéma + upsert + UNIQUE.

- [ ] **Step 4: Commit**

```
feat(db): chapitres mensuels par personne
```

---

### Task 4: Job `person_history`

**Files:**
- Create: `scripts/person_history.py`
- Modify: `scripts/ingestion_worker.py`
- Modify: `database/ingestion.py`
- Modify: `config.py` + `.env.example` (plafonds)

- [ ] **Step 1:** File `enqueue(job_type="person_history", payload={person_id?, year_month?})`.

- [ ] **Step 2:** Worker : pour chaque personne priorisée (spec), pour le mois cible :

1. Compter messages `imessage_messages` via handle(s) de la personne, bornes `time_buckets`.
2. Si 0 → upsert `empty`.
3. Stats déterministes (`ContactAnalytics` réutilisé si déjà calculable pour une fenêtre).
4. Échantillon ≤ 400 messages.
5. LLM **rapide** → JSON highlights + narrative ; parse strict ; repli déterministe (stats + extraits, sans prose inventée). La synthèse multi-mois au moment de la question utilise le **modèle principal**.
6. `content_hash` ; skip si inchangé.
7. `event_bus.emit` `person.chapter_updated` **après commit**.

Jamais `chat.db`. Timeouts LLM existants. `PERSON_HISTORY_MAX_CHAPTERS_PER_RUN=8`.

- [ ] **Step 3:** Tests : mois vide → empty ; hash skip ; JSON invalide → repli ; pas d’appel réseau (mock `llm.chat`).

- [ ] **Step 4: Commit**

```
feat(ingestion): job person_history — un chapitre par mois et par personne
```

---

### Task 5: Brancher retrieval + chat + API + events

**Files:**
- Modify: `jarvis/retrieval/coordinator.py` (source `person_month`)
- Modify: `api/chat_context.py`
- Modify: `api/router_people.py`
- Modify: `jarvis/events.py` + tests event types
- Modify: `frontend/src/lib/api.ts` seulement si un écran Contacts consomme déjà people (sinon reporter UI)

- [ ] **Step 1:** Indexer les chapitres dans le retrieval (titre = `Chapitre {name} {year_month}`).

- [ ] **Step 2:** `search_knowledge("histoire avec AliceTest")` :

- hits `person_month` présents
- pas de flood iMessage
- digest histoire = concat chapitres

- [ ] **Step 3:** Si 0 chapitre et messages > 0 : enqueue `person_history` ; contexte `history_pending: true` ; réponse courte (spec).

- [ ] **Step 4:** `GET /api/people/{name}/history` ; `POST .../history/rebuild` (202 + job id). Tests route contract Phase 4.

- [ ] **Step 5:** Event type déclaré **avant** le bloc domaine si `DOMAIN_EVENT_TYPES` est « les N derniers » — même piège que `food.order_updated` / `task.control.*`.

- [ ] **Step 6: Commit**

```
feat(people): synthèse d’histoire depuis les chapitres mensuels
```

---

### Task 6: Voix, persona, garde-fous

**Files:**
- Modify: `prompts/persona.txt` (une phrase : identité vs histoire ; ne pas dire « index partiel » si iMessage `complete`)
- Modify: tests persona / voice si existants
- Optionnel: `api/voice_processing.py` — raccourcir encore plus en vocal (3 phrases)

- [ ] Ne pas inventer de faits hors chapitres + dossier.
- [ ] Citations : seulement `quote` des highlights (déjà borné).

- [ ] **Commit**

```
fix(persona): ne plus hedger « index partiel » sur une source complète
```

---

## Validation globale

```bash
python -m pytest tests/test_person_history.py tests/test_retrieval_coordinator.py tests/test_phase4_route_contract.py -q
```

Pas de test qui lit `~/Library/Messages/chat.db`.
Pas de log de contenu iMessage.

---

## Hors de ce plan (volontaire)

- Fusion automatique de fiches people
- Diarisation
- OpenCode / runtime agentique pour SQL personnel
- UI Contacts dédiée (peut suivre)
- Relance massive de `relationship_analyzer` sur 5000 messages
