# Configuration

Les valeurs par défaut sont dans `config/defaults.json`; la configuration
fournisseur générée est `config/opencode.json`.

## DeepSeek — une seule source de vérité

`DEEPSEEK_API_KEY` est définie **une seule fois** dans la configuration
JARVIS (fichier secrets ``.env``, chargé après ``.env.config`` via
``env_loader.load_jarvis_env`` / ``config``). L'adaptateur OpenCode :

1. relit cette valeur depuis l'environnement JARVIS déjà chargé ;
2. la transmet au processus enfant uniquement via l'allowlist explicite
   (`DEEPSEEK_API_KEY`) ;
3. ne la persiste jamais dans `integrations/opencode/.runtime/config/*.json`,
   les logs, argv, événements ou artefacts.

Aucune variable concurrente du type `OPENCODE_DEEPSEEK_API_KEY`, aucun
``.env`` spécifique OpenCode, et aucun argument CLI ne sont supportés. Sans
clé JARVIS valide, le runtime refuse le provider anonyme intégré `opencode`
et échoue avec une erreur explicite pointant vers ``.env``.

L'adaptateur transmet explicitement `DEEPSEEK_API_KEY` lorsqu'elle est
configurée ; aucune autre variable secrète du parent n'est héritée par le
processus enfant.

| Variable | Valeur / rôle |
|---|---|
| `DEEPSEEK_API_KEY` | Secret JARVIS unique (``.env``) ; forwardé à OpenCode via allowlist |
| `AGENTIC_RUNTIME` | `auto`, identifiant d'un runtime, ou `disabled` |
| `AGENTIC_RUNTIME_FALLBACK` | `disabled` par défaut ; `legacy` uniquement sur opt-in |
| `AGENTIC_DEFAULT_PROFILE` | profil de capacités de repli, `readonly-research` par défaut |
| `AGENTIC_PROFILE_ROUTE_OVERRIDES` | objet JSON optionnel `catégorie -> profil` ; ne peut pas élargir les scopes du profil |
| `OPENCODE_DISABLE_PROJECT_CONFIG` | imposé à `true` dans l'enfant |
| `OPENCODE_CONFIG` | copie privée générée pour le run |
| `OPENCODE_DISABLE_AUTOUPDATE` | imposé à `true` |
| `DEVAGENT_AUTO_PR` | autorise la livraison JARVIS en draft pour les boucles automatisées |
| `DEVAGENT_REQUIRED_CHECKS` | tableau JSON explicite des checks requis ; vide utilise les sept checks JARVIS, preuve de retrait complète incluse, uniquement pour le dépôt JARVIS |
| `GH_TOKEN` ou `GITHUB_TOKEN` | jeton du parent pour REST et le push ; jamais transmis au runtime, à argv, à l'environnement Git ou à un fichier |

Les mots de passe Basic Auth et tokens MCP ne doivent jamais être placés dans
`.env`, les prompts, les événements, les notifications ou les arguments de
processus. La clé modèle peut provenir du `.env` local ignoré ou d'un
gestionnaire de secrets, mais n'est jamais persistée dans la configuration
générée, les événements ou les logs. Les budgets sont fournis par `RunBudget` :
durée, deadline, steps, appels outils, retries, tokens modèle, contexte, coût
optionnel, taille d'artefacts et concurrence.

## Profils de capacités JARVIS

Le routeur choisit avant le démarrage l'un des huit profils provider-neutral :
`readonly-research`, `coding`, `communication`, `browser`, `invoice`, `obs`,
`media` ou `desktop`. Cette décision est persistée sous
`selected_context.capability_profile_id`, distinct du `profile_id` utilisateur
qui continue d'isoler les bases. Le service refuse tout scope hors du profil
avant d'appeler OpenCode, puis vérifie aussi que le runtime déclare chaque
scope demandé.

`AGENTIC_PROFILE_ROUTE_OVERRIDES` permet à l'administrateur de choisir un
profil existant pour une catégorie, par exemple
`{"workflow":"readonly-research","agentic_reversible":"coding"}`. Une route
incompatible (par exemple `agentic_readonly -> coding`) est refusée : la
configuration et le texte du prompt ne peuvent jamais provoquer une élévation.
Les opérations sensibles restent soumises à approbation (`send`, live public,
publication média), tandis que shell libre, push/merge/déploiement, action
financière et élévation de privilège demeurent hors profil.

La publication reste fail-closed : sans jeton parent ou
liste de checks valide pour un dépôt tiers, JARVIS conserve le commit local et
n'ouvre aucune PR. L'API GitHub est appelée directement par le parent avec la
version `2026-03-10`; le binaire `gh` n'est ni requis ni exécuté. Pour le push
exact, Git travaille depuis un dépôt bare éphémère sans config locale/globale
et un askpass privé lit le jeton via un descripteur one-shot. Le jeton doit
être injecté par le gestionnaire de secrets du service, jamais committé ni
placé dans un argument, une variable d'environnement enfant ou un fichier.
