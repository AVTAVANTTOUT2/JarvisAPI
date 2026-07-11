# 25 — Processus de Revue d'Architecture

**Date** : 11 juillet 2026
**Statut** : Checklist obligatoire avant tout refactoring majeur

---

## Quand utiliser cette checklist

Avant chaque :
- Refactoring impactant plus de 3 fichiers
- Ajout d'un nouveau service
- Changement d'une interface publique
- Modification d'une règle de gouvernance
- Introduction d'une nouvelle dépendance externe

## Checklist de revue d'architecture

### 1. Alignement avec la vision

```
[ ] Cette modification respecte-t-elle la vision JARVIS ? (00_VISION.md)
[ ] Respecte-t-elle les 11 principes non négociables ?
[ ] Si non, une dérogation est-elle documentée dans un ADR ?
```

### 2. Source de vérité

```
[ ] La modification crée-t-elle une nouvelle source de vérité ?
[ ] Si oui, est-elle documentée dans 09_DATA_OWNERSHIP.md ?
[ ] La modification viole-t-elle le principe de propriétaire unique ?
```

### 3. Couplage

```
[ ] La modification augmente-t-elle le couplage entre modules ?
[ ] Introduit-elle une nouvelle dépendance entre services ?
[ ] Pourrait-elle être implémentée via l'Event Bus plutôt qu'un appel direct ?
[ ] Respecte-t-elle les règles de dépendances (21_DEPENDENCY_RULES.md) ?
```

### 4. Dette technique

```
[ ] La modification augmente-t-elle la dette technique ?
[ ] Introduit-elle de la duplication ?
[ ] Crée-t-elle un nouveau god object (>500 lignes) ?
[ ] Une entrée dans 23_TECHNICAL_DEBT.md est-elle nécessaire ?
```

### 5. ADR

```
[ ] La modification nécessite-t-elle un nouvel ADR ?
[ ] Un ADR existant doit-il être mis à jour ?
[ ] La modification est-elle cohérente avec les ADR existants ?
```

### 6. Migration

```
[ ] La modification nécessite-t-elle une migration de données ?
[ ] Le plan de migration (05_PLAN_MIGRATION.md) doit-il être mis à jour ?
[ ] La rétrocompatibilité est-elle assurée ?
```

### 7. Tests

```
[ ] Des tests supplémentaires sont-ils nécessaires ?
[ ] La couverture de tests ne doit PAS baisser
[ ] Les Fitness Functions (22_FITNESS_FUNCTIONS.md) passent-elles ?
```

### 8. Documentation

```
[ ] La documentation (README, CLAUDE.md) doit-elle être mise à jour ?
[ ] Les contrats internes (20_CONTRATS_INTERNES.md) sont-ils impactés ?
[ ] Les contrats API (16_CONTRATS_API.md) sont-ils impactés ?
```

### 9. Sécurité

```
[ ] La modification introduit-elle un nouveau vecteur d'attaque ?
[ ] Les données personnelles sont-elles protégées ?
[ ] Les permissions macOS sont-elles suffisantes ?
```

### 10. Performance

```
[ ] La modification dégrade-t-elle les performances ?
[ ] Un benchmark avant/après est-il nécessaire ?
```

## Résultat de la revue

```
[ ] REVUE APPROUVÉE — Tous les critères sont satisfaits
[ ] REVUE CONDITIONNELLE — Points mineurs à corriger, peut procéder
[ ] REVUE REFUSÉE — Points bloquants, doit être retravaillé

Points bloquants :
1. ...
2. ...

Actions requises avant de pouvoir procéder :
1. ...
2. ...
```

## Fréquence

- **Revue légère** (checklist rapide) : chaque PR modifiant >100 lignes
- **Revue complète** (toute la checklist) : chaque phase de refactoring, chaque nouveau service
