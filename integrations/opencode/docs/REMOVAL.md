# Désinstallation et preuve de suppression

## Deux niveaux distincts

`uninstall` énumère de façon bornée les serveurs racine et per-run, refuse tout
état ambigu (lien, propriétaire/mode/PID/binaire non attribuable), arrête chaque
processus dont la propriété est prouvée, puis seulement supprime tout
`.runtime/` : binaire, configuration, HOME/XDG, cache, état, capacités, auth,
logs et temporaires. Le code du plugin reste disponible.

La suppression complète retire ensuite `integrations/opencode/`. Le cœur
JARVIS redécouvre alors zéro instance de ce fournisseur, résout `auto` vers
aucun runtime disponible, et persiste les nouvelles demandes agentiques en
`provider_unavailable`. Les actions déterministes et les canaux ordinaires
restent utilisables ; aucun fallback agentique implicite n'est activé.

## Procédure sûre

Depuis la racine exacte du worktree, avec une branche récupérable :

```bash
python -m integrations.opencode.scripts.manager status
python -m integrations.opencode.scripts.manager uninstall
python -m integrations.opencode.tools.removal_proof
git rm -r -- integrations/opencode
```

L'ordre est impératif. Ne pas faire `git rm` avant `uninstall` : la suppression
de l'état privé retirerait la preuve nécessaire à l'arrêt sûr d'un éventuel
daemon. La commande `uninstall` est idempotente et refuse de suivre un lien.
Conserver `AGENTIC_RUNTIME=auto` et `AGENTIC_RUNTIME_FALLBACK=disabled` pour un
état absent explicite et sans ancien backend caché.

Après `git rm`, valider le contrat générique :

```bash
python -m pytest tests/ jarvis/tests agents/devagent -q
python tools/export_openapi.py --check
python tools/generate_python_sdk.py --check
python tools/audit_architecture_truth.py --check
python tools/audit_technical_debt.py --check
(cd web && pnpm test && pnpm typecheck)
(cd frontend && pnpm test && pnpm typecheck && pnpm build)
```

Les applications Android/macOS et toute CI du dépôt doivent aussi compiler
selon leurs commandes officielles. Aucun import, type, endpoint, DTO ou écran
de production ne doit dépendre du nom du fournisseur supprimé.

## Preuve automatisée fournie

Commande exacte :

```bash
python -m integrations.opencode.tools.removal_proof
```

Le test CI équivalent est :

```bash
python -m pytest integrations/opencode/tests/test_removal_proof.py -q
```

La preuve :

1. vérifie qu'aucun code, manifeste, client ou document de production hors du
   dossier plugin ne contient de référence au fournisseur ; les tests de
   non-régression peuvent le nommer pour affirmer son absence ;
2. crée un `TemporaryDirectory`, copie le dépôt sans `.git`, secrets, données,
   dépendances, artefacts, caches ni liens symboliques ;
3. crée dans la copie des marqueurs représentant binaire, PID et auth, puis
   valide strictement que la cible est un enfant du temporaire avant de
   supprimer **uniquement** la copie du plugin ;
4. lance un Python isolé avec une base SQLite et des uploads eux aussi placés
   dans le temporaire ;
5. compile en mémoire le code Python de production et importe le cœur
   agentique, API, chat, WebSocket, voix, mobile et iMessage ;
6. initialise le schéma, contrôle les huit tables agentiques, génère et
   sérialise l'OpenAPI, et vérifie les endpoints génériques ;
7. confirme que le registre ne découvre pas le fournisseur, qu'un run termine
   proprement en `provider_unavailable` et qu'une demande directe ordinaire
   n'est pas détournée ;
8. utilise l'audit Python pour prouver qu'aucun sous-processus/daemon ni accès
   réseau n'est lancé pendant ces imports, puis cherche tout fichier runtime
   résiduel ;
9. détruit le temporaire et confirme que le plugin du worktree source existe
   toujours.

La preuve est entièrement hors ligne. Elle ne prétend pas remplacer les builds
natifs complets : elle prouve leur indépendance statique au fournisseur, puis
la CI exécute les compilateurs réels.

## Critères d'acceptation de la suppression

- aucun processus appartenant au plugin et aucun `.runtime` résiduel ;
- aucun manifeste du fournisseur découvert et aucun module chargé ;
- import de `main`, initialisation DB et OpenAPI réussis ;
- API et canaux génériques importables, demandes directes inchangées ;
- demande agentique sans runtime visible comme indisponible, jamais comme un
  faux succès ;
- tests, typage, builds, audits et génération de contrats verts ;
- aucune donnée utilisateur ou secret supprimé hors du dossier plugin.

Rollback tant que le commit n'est pas fusionné : restaurer le dossier depuis
Git, réinstaller avec le manifeste épinglé, puis exécuter `verify` et
`smoke-test`. Les anciens fichiers runtime ne sont pas restaurés par Git et ne
doivent pas être récupérés depuis des logs ou une archive non vérifiée.
