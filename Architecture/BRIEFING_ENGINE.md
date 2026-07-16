# Moteur de briefings

Dernière mise à jour : 2026-07-16

## Rôle

Produire des briefings structurés (matin, soir, delta, work_only, voice_only) à partir des données déjà en base et des intégrations Apple — priorisation explicite, version vocale courte séparée du texte complet.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `agents/briefing_engine.py` | Collecte, priorisation, dédup, LLM synthèse |
| `agents/productivity.py` | `morning_briefing` / evening délèguent au moteur |
| `api/voice_cognitive.py` | Variantes vocales + filtres |
| `api/router_cognitive.py` | `POST /api/briefings/generate` |

## Modèle de données

- `BriefingItem` — titre, détail, `priority` ∈ {critique, aujourd_hui, surveiller, information}, source, freshness, actions
- `StructuredBriefing` — `kind`, `items`, `full_text`, `voice_text`, `unavailable`

Déduplication par `dedupe_key` (source + titre).

## Kinds supportés

| Kind | Contenu |
|------|---------|
| `morning` | Agenda, mails pré-analysés, tâches, notifs, commitments |
| `evening` | Bilan journée + reste à faire |
| `delta` | Différence vs snapshot matin (persisté côté moteur) |
| `work_only` | Filtre travail / école |
| `voice_only` | Synthèse ultra-courte pour TTS |
| filtres urgents | Via détection variante vocale |

## Flux

```
generate_briefing(kind, filters...)
  → collecte déterministe (DB + résumés email + calendar si dispo)
  → priorisation + dédup
  → DeepSeek Main pour full_text (si besoin)
  → voice_text court (Flash ou troncature contrôlée)
  → StructuredBriefing
```

Productivité historique : `morning_briefing()` / evening appellent le moteur au lieu de dupliquer la logique.

## Endpoint

`POST /api/briefings/generate` — body `{ "kind": "morning"|"evening"|"delta"|..., "filters": {...} }`

Voix : motifs « briefing », « version courte », « seulement les urgences », « qu’est-ce qui a changé ».

## Config

Utilise les modèles globaux :

```bash
MAIN_REASONING_MODEL=    # synthèse complète
VOICE_REASONING_MODEL=   # formulation courte
```

Pas de flag dédié au-delà des intégrations Mail/Calendar existantes.

## Limites connues

- Si Mail.app / Calendar.app indisponibles, les sources apparaissent dans `unavailable` plutôt que d’inventer du contenu.
- Le snapshot matin pour `delta` dépend d’un briefing matin déjà généré le même jour.
- Les commitments viennent de la table/API commitments existante ; absence = section omise.
