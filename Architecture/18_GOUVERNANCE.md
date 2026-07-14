# 18 — Gouvernance du Projet

**Date** : 11 juillet 2026
**Statut** : Règles d'architecture

---

## Architecture Rules (Règles absolues)

Ces règles sont **non négociables**. Toute violation doit être justifiée par un ADR.

### Règle 1 — Accès aux données Apple

> **Aucun accès direct à `chat.db` en dehors du `AppleDataService`.**

La Phase 5 applique cette règle à Messages.app uniquement. La centralisation des autres sources natives (Contacts, Calendar, Mail et Notes) reste une cible d'architecture et ne doit pas être présentée comme déjà réalisée.

- Violation : `sqlite3.connect("...chat.db")` dans un fichier autre que `integrations/apple_data.py`
- Détection : `python -m pytest tests/test_apple_data.py -q` (analyse AST des accès exécutables)
- Sanction : PR refusée

### Règle 2 — Accès à SQLite

> **Aucun accès direct à `jarvis.db` en dehors des modules `database/*.py`.**

- Violation : `sqlite3.connect` ou `get_db()` dans un fichier hors `database/`
- Exception : Les tests (avec mock) et `apple_data.py` (qui utilise `chat.db`, pas `jarvis.db`)
- Détection : Code review
- Sanction : PR refusée

### Règle 3 — Duplication

> **Aucune duplication de logique métier.**

- Définition : Deux fonctions qui font la même chose dans deux fichiers différents
- Exception : Code boilerplate (routes FastAPI similaires)
- Détection : `scripts/duplicate_scanner.py`
- Sanction : Refactoring avant merge

### Règle 4 — Source unique de vérité

> **Chaque donnée a UN et UN SEUL propriétaire (voir ADR-011).**

- Violation : Écriture dans une table par un module non propriétaire
- Détection : Code review + `grep "INSERT INTO people"` hors `database/people.py`
- Sanction : PR refusée

### Règle 5 — Tests obligatoires

> **Toute nouvelle fonctionnalité doit avoir des tests.**

- Minimum : Tests unitaires pour les fonctions pures, tests d'intégration pour les routes API
- Exception : Code purement cosmétique (CSS)
- Détection : Code review
- Sanction : PR refusée

### Règle 6 — Documentation

> **Tout nouveau module doit être documenté.**

- Minimum : Docstring sur la classe/fonction principale, une ligne dans le README ou CLAUDE.md
- Exception : Modules internes de moins de 50 lignes
- Détection : Code review
- Sanction : Documentation ajoutée avant merge

### Règle 7 — ADR obligatoire

> **Toute décision d'architecture impactant plus d'un module doit être accompagnée d'un ADR.**

- Format : `Architecture/adr/ADR-XXX-titre.md`
- Contenu : Problème, solutions envisagées, décision, conséquences
- Détection : Code review
- Sanction : ADR rédigé avant merge

### Règle 8 — Taille des modules

> **Aucun module ne doit dépasser 1000 lignes.**

- Actuel après Phase 4 : `main.py` fait 175 lignes et tous les modules `api/` restent à 500 lignes ou moins ; `database/` culmine à 666 lignes et sa façade fait 236 lignes
- Cible : max 500 lignes par module impératif nouvellement créé ; les modules déclaratifs historiques doivent être justifiés
- Exception : `schema.sql` (déclaratif), fichiers de tests
- Détection : `wc -l *.py | sort -rn | head -10`
- Sanction : Split avant merge

### Règle 9 — Pas de lazy import

> **Tous les imports doivent être top-level. Les lazy imports sont interdits sauf justification documentée.**

- Exception : Imports conditionnels (ex: `try: import foo except ImportError: foo = None`)
- Justification valide : Éviter une dépendance circulaire (mais la dépendance elle-même doit être documentée dans un ADR)
- Détection : `grep -r "from.*import" --include="*.py" | grep "def \|async def" | grep -B1 import`

### Règle 10 — Gestion d'erreurs

> **Pas de `except Exception` nu. Toujours spécifier le type d'exception attendu.**

- Exception : Top-level error handler (ex: boucle principale du daemon)
- Minimum : `except (ValueError, KeyError) as e:` + log de l'erreur
- Détection : `grep -r "except Exception" --include="*.py" | grep -v "test_\|# noqa"`
- Sanction : Spécifier le type avant merge

### Règle 11 — Pas de secrets dans le code

> **Toutes les clés API, tokens, et secrets sont dans `.env`.**

- Détection : `scripts/security_audit.py` (existant)
- Sanction : PR refusée — bloquante

### Règle 12 — Pas d'appel direct aux LLM

> **Aucun module ne doit appeler directement l'API DeepSeek ou Ollama. Tout passe par `ai_service`.**

- Détection : `grep -r "deepseek\|openai\|ollama" --include="*.py" | grep -v ai_service | grep -v llm.py | grep -v config.py | grep -v test`
- Sanction : PR refusée

## Checklist de merge

Avant de merger une PR :

```
[ ] Tous les tests passent (CI verte)
[ ] Couverture ≥ avant la PR
[ ] Pas de violation des règles 1-12
[ ] ADR mis à jour si nécessaire
[ ] Documentation mise à jour
[ ] Code review approuvée
[ ] Plan de rollback documenté
[ ] Pas de régression de performance
[ ] CHANGELOG mis à jour
```

## Vérification automatique

Le script général `scripts/architecture_check.py` décrit ci-dessous reste une cible : il n'existe pas encore dans le dépôt et ne doit pas être présenté comme exécuté. Les règles API sont matérialisées dans `tests/test_phase4_architecture.py` et la règle `chat.db` dans `tests/test_apple_data.py`.

```bash
# Vérifie les règles 1, 2, 3, 8, 9, 11, 12
python scripts/architecture_check.py

# Sortie :
# [✓] Règle 1: Aucun accès direct à chat.db hors apple_data
# [✓] Règle 2: Aucun accès direct à jarvis.db hors database/
# [✓] Règle 3: Aucune duplication détectée
# [✓] Règle 8: main.py et tous les modules api/ restent sous le seuil Phase 4
# [✓] Règle 9: Aucun lazy import détecté
# ...
```
