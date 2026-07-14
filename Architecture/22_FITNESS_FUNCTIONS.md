# 22 — Architecture Fitness Functions

**Date** : 11 juillet 2026
**Statut** : Règles automatisables — implémentation partielle ; contrôles API Phase 4 actifs dans pytest

---

## Définition

Les Fitness Functions sont des règles vérifiables automatiquement qui garantissent que l'architecture reste conforme aux principes définis. Le script général présenté ci-dessous est une cible et n'existe pas encore. Depuis la Phase 4, `tests/test_phase4_architecture.py` exécute les contrôles de taille et de dépendance inverse de la couche API dans la suite pytest.

## Règles automatiques

### F-01 — Pas de dépendance circulaire

```bash
python scripts/architecture_check.py --check-cycles
```

**Seuil** : 0 cycle détecté
**Action si échec** : PR refusée

### F-02 — Taille maximale des modules

```bash
python scripts/architecture_check.py --check-size --max-lines 500
```

**Seuil** : Aucun nouveau fichier > 500 lignes sans justification dans un ADR
**Action si échec** : Warning — le module doit être split ou justifié

**État Phase 4** : actif pour `main.py` et `api/*.py` via pytest (seuil 500 lignes).

### F-03 — Pas plus de 10 responsabilités par module

```bash
python scripts/architecture_check.py --check-responsibilities --max 10
```

**Seuil** : Une « responsabilité » = un groupe de fonctions liées à un domaine
**Action si échec** : Warning — le module doit être split

### F-04 — Aucune nouvelle lecture directe de chat.db

```bash
python -m pytest tests/test_apple_data.py -q
```

**Seuil** : aucune expression exécutable ne reconstruit le chemin de Messages et aucun `sqlite3.connect` ne vise `chat.db` hors `integrations/apple_data.py`; la conversion canonique y est définie une seule fois.
**Action si échec** : PR refusée

### F-05 — Aucune duplication de logique métier

```bash
python scripts/duplicate_scanner.py --check
```

**Seuil** : 0 nouvelle duplication (tolérance pour l'existant documenté)
**Action si échec** : Warning — la duplication doit être résolue ou documentée dans TECH_DEBT

### F-06 — Couverture de tests minimale

```bash
python -m pytest tests/ --cov=. --cov-report=term --cov-fail-under=60
```

**Seuil** : 60% minimum global, la couverture ne doit PAS baisser vs la PR précédente
**Action si échec** : PR refusée

### F-07 — Respect des couches d'architecture

```bash
python scripts/architecture_check.py --check-layers
```

Vérifie :
- Pas d'import `database/` depuis `web/`
- Pas d'import `main` depuis `database/`
- Pas d'appel `llm.chat()` hors `ai_service`
- Aucun appel direct à `create_notification()` dans les producteurs `agents/` et `scripts/` ; la façade `database/notifications.py` reste compatible et délègue à `notification_service`

**Action si échec** : PR refusée

**État Phase 4** : l'interdiction `api → main` est active via analyse AST dans pytest ; les autres couches attendent le script général.

### F-08 — Pas de lazy import non justifié

```bash
python scripts/architecture_check.py --check-lazy-imports
```

**Seuil** : 0 nouveau lazy import sans commentaire `# noqa: ARCH-LAZY` + ADR
**Action si échec** : Warning

### F-09 — Documentation mise à jour

```bash
python scripts/architecture_check.py --check-docs
```

Vérifie que les nouveaux modules ont un docstring.
**Action si échec** : Warning

### F-10 — Pas de régression de performance

```bash
python scripts/architecture_check.py --check-perf
```

Compare le temps d'exécution des tests vs la baseline.
**Seuil** : Pas d'augmentation > 20%
**Action si échec** : Warning

## Implémentation CI cible

```yaml
# .github/workflows/architecture-check.yml
name: Architecture Fitness
on: [pull_request]

jobs:
  fitness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python scripts/architecture_check.py --check-cycles
      - run: python scripts/architecture_check.py --check-size --max-lines 500
      - run: python scripts/architecture_check.py --check-chatdb-access
      - run: python scripts/duplicate_scanner.py --check
      - run: python -m pytest tests/ --cov=. --cov-fail-under=60
      - run: python scripts/architecture_check.py --check-layers
```

## Évolution des règles

Les Fitness Functions évoluent avec le projet. Une règle peut être :
- **Ajoutée** : quand un nouveau principe est défini
- **Durcie** : quand le seuil est augmenté (ex: couverture 60% → 70%)
- **Assouplie** : uniquement via un ADR justifié
- **Supprimée** : si elle n'est plus pertinente
