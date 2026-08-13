# ADR-033 — JARVIS propriétaire unique de Git

- Statut : accepté
- Date : 2026-08-13

## Contexte

Le runtime agentique peut éditer un worktree. S'il committait, poussait ou
ouvrait une PR, JARVIS perdrait la politique Git, le secret scan et le
caractère draft.

## Décision

Le provider d'exécution n'a jamais : secrets GitHub, politique Git, création
de worktrees, staging, commits, pushes, PR, merges, déploiements, décision
`completed`.

Le flux reste :

```text
artefacts attestés → validation choisie par JARVIS → secret scan
→ reviewer → finalizer → staging exact → commit JARVIS
→ push JARVIS → draft PR JARVIS
```

La commande de validation n'est pas choisie par le modèle. Pas d'auto-merge,
pas de force-push, pas de PR hors draft par défaut, pas de base inattendue.

Le sandbox macOS 26 autorise la lecture du venv JARVIS, les métadonnées des
ancêtres, `/dev/null` et l'entrée `literal "/"` — pas `.env`, pas le home,
pas les clés SSH, pas l'écriture du venv, pas le réseau.

## Conséquences

Retirer le plugin d'exécution ne change pas la politique Git. DevAgent reste
le livreur.
