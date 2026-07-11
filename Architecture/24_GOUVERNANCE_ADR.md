# 24 — Gouvernance des ADR

**Date** : 11 juillet 2026
**Statut** : Processus — tout ADR doit suivre ce cycle de vie

---

## Cycle de vie d'un ADR

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ PROPOSÉ  │────▶│ ACCEPTÉ  │────▶│IMPLÉMENTÉ│────▶│ ARCHIVÉ  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
      │                │                │                │
      │                │                │                │
      ▼                ▼                ▼                ▼
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ REJETÉ   │     │DÉPRÉCIÉ  │     │SUPERSEDÉ │     │ ABANDONNÉ│
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

## Quand créer un ADR ?

Un ADR est **obligatoire** quand :

1. Une décision impacte l'architecture de plus d'un module
2. Un choix technique a des implications long terme (>6 mois)
3. Une dépendance externe est ajoutée ou supprimée
4. Un pattern architectural est introduit (ex: Event Bus, CQRS, Plugin system)
5. Une règle de gouvernance est modifiée
6. Une dérogation aux règles existantes est nécessaire

Un ADR n'est **pas nécessaire** pour :
- Un refactoring local (un seul fichier)
- Un bug fix
- Une optimisation sans changement d'architecture
- L'ajout d'un endpoint REST (sauf si nouveau pattern)

## Quand modifier un ADR ?

Un ADR est modifié quand :
- L'implémentation révèle des contraintes non anticipées
- Une meilleure solution est découverte avant implémentation
- Un détail de l'ADR s'avère incorrect

La modification crée une **nouvelle version** de l'ADR (v1 → v2). L'ancienne version est conservée dans l'historique.

## Quand remplacer un ADR ?

Un ADR est remplacé (superseded) quand :
- La solution choisie n'est plus viable (obsolète techniquement)
- Une décision plus fondamentale rend l'ADR caduc
- L'ADR est remplacé par un ADR plus général

L'ADR remplacé passe en statut `SUPERSEDED` avec une référence vers le nouvel ADR.

## Quand déprécier un ADR ?

Un ADR est déprécié quand :
- La fonctionnalité qu'il documente est supprimée
- La technologie qu'il décrit est retirée
- Le pattern qu'il définit n'est plus utilisé

L'ADR déprécié est conservé pour l'historique mais marqué `DEPRECATED`.

## Format d'un ADR

```markdown
# ADR-XXX — Titre

**Date** : YYYY-MM-DD
**Statut** : PROPOSÉ | ACCEPTÉ | IMPLÉMENTÉ | DÉPRÉCIÉ | SUPERSEDED | REJETÉ
**Supersedes** : ADR-YYY (si applicable)
**Superseded by** : ADR-ZZZ (si applicable)

## Contexte
Pourquoi cette décision est nécessaire.

## Décision
Ce qui a été décidé.

## Alternatives considérées
Solutions rejetées et pourquoi.

## Conséquences
Ce qui devient plus facile, plus difficile, ce qu'il faut surveiller.
```

## Règles

1. **Un ADR par décision** — pas de décision multiple dans un ADR
2. **Numérotation séquentielle** — ADR-001, ADR-002, ...
3. **Jamais supprimer un ADR** — archiver, déprécier, ou superseder
4. **L'ADR est la vérité** — si le code contredit l'ADR, c'est le code qui a tort
5. **Revue périodique** — tous les trimestres, revoir les ADR PROPOSÉ pour les accepter ou rejeter
