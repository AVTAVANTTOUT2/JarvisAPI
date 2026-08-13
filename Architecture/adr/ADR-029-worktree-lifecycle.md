# ADR-029 — Cycle de vie borné des worktrees agentiques

- Statut : accepté
- Date : 2026-08-13

## Contexte

Les runs agentiques créent des worktrees sous `.jarvis/worktrees/` sans
politique de nettoyage. Les copies s'accumulent, y compris des arbres sales
ou non poussés.

## Décision

`jarvis/agentic/worktrees.py` est la seule autorité provider-neutral.
Le runtime agentique ne crée ni ne détruit aucun worktree.

Un worktree n'est retiré automatiquement que s'il est propre, sans non-suivi,
avec commits uniques poussés ou sauvegardés, hors PR ouverte, hors run actif,
hors processus, et strictement sous la racine autorisée. Sinon il est retenu
(`retained_dirty`, `retained_unpushed`, `retained_manual`) avec une preuve
bornée (manifest, hashes, patch redacté). Aucun commit WIP implicite.

Le GC est dry-run par défaut (`python -m jarvis.agentic.worktrees gc`).
`--apply` est explicite. Inventaire JSON privé `inventory.json` (0600).

## Conséquences

- TTL, nombre max et taille globale sont configurables.
- Un doute sur le chemin (symlink, hors racine) interdit la suppression.
- DevAgent enregistre `active` à la création et `delivered` après commit
  JARVIS.
