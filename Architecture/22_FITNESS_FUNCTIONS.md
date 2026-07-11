# 22 — Architecture Fitness Functions

**Date** : 11 juillet 2026
**Statut** : Règles automatisables — à intégrer dans la CI

---

## Définition

Les Fitness Functions sont des règles vérifiables automatiquement qui garantissent que l'architecture reste conforme aux principes définis. Elles sont exécutées à chaque PR et en CI.

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

### F-03 — Pas plus de 10 responsabilités par module

```bash
python scripts/architecture_check.py --check-responsibilities --max 10
```

**Seuil** : Une « responsabilité » = un groupe de fonctions liées à un domaine
**Action si échec** : Warning — le module doit être split

### F-04 — Aucune nouvelle lecture directe de chat.db

```bash
python scripts/architecture_check.py --check-chatdb-access
```

**Seuil** : `chat.db` n'est référencé que dans `integrations/apple_data.py` et les tests
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
- Pas d'appel `create_notification()` hors `notification_service`

**Action si échec** : PR refusée

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

## Implémentation CI

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
