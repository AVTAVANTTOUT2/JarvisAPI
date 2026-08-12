# Agents OpenCode gérés par JARVIS

| Agent | Édition | Shell | Usage |
|---|---:|---:|---|
| `jarvis-planner` | refusée | refusé | plan borné |
| `jarvis-executor` | approbation JARVIS | refusé | exécution réversible |
| `jarvis-reviewer` | refusée | refusé | revue des preuves |
| `jarvis-coding` | autorisée dans le worktree | refusé | édition de code |

Le shell natif reste refusé pour les quatre agents. Les tests, Git, push,
draft PR et checks CI sont exécutés par JARVIS après comparaison exacte du
manifeste `path + size + SHA-256`. Les commandes Git de commit/push/merge ou
rebase ne sont jamais une capacité du runtime.

La destination GitHub, la branche de base, le SHA de tête et l'ensemble des
checks requis sont capturés avant délégation puis persistés de manière
immuable. Le push utilise un dépôt bare éphémère sans configuration héritée ;
la création de PR draft et la lecture des checks utilisent l'API REST dans le
parent. Aucun jeton n'entre dans OpenCode, argv, l'environnement Git, un
fichier ou un log ; l'askpass borné le reçoit uniquement via un descripteur
one-shot détenu par le parent.
