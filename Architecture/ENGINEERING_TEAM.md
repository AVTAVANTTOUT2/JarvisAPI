# Équipe d'ingénierie autonome JARVIS

## Chaîne de responsabilité

- **Cursor** détecte les vulnérabilités, bugs, incohérences, régressions CI,
  trous de couverture et documentation obsolète. Il publie une PR ou un constat
  Slack.
- **GitHub** est la file de travail canonique. Les handoffs portent les labels
  `cursor-finding`, `agent-ready` et `agent-managed`.
- **Codex** est le tech lead, orchestrateur et développeur principal. Il audite
  systématiquement le travail Cursor, corrige si nécessaire et exécute les tests.
- **Claude Pro** effectue la dernière revue du SHA exact, strictement en lecture
  seule.
- **Le runner déterministe** fusionne automatiquement après le verdict Claude et
  une CI GitHub entièrement verte. Aucun LLM n'exécute directement le merge.

Codex et Claude utilisent uniquement leurs sessions d'abonnement locales. Le
runner retire explicitement les variables de clés API de leur environnement.

## Entrées Cursor

La boucle reconnaît automatiquement :

1. toute PR ouverte vers `main` dont l'auteur GitHub figure dans
   `cursor.trusted_pr_authors` (`app/cursor` par défaut) ;
2. toute Issue `agent-ready`, notamment les Issues de sécurité Cursor ;
3. les constats Cursor publiés dans les canaux Slack Jarvis : l'automatisation
   Codex crée une Issue GitHub si aucun lien GitHub n'existe encore.

La PR Cursor est prioritaire sur une nouvelle Issue. Les messages Slack servent
de filet de rattrapage pour les automations report-only comme le scan sécurité.

Le label `cursor-finding` **n'admet pas** à lui seul une PR : il ne prouve rien
sur l'origine du code ni sur celle de la description, que Codex lit ensuite en
écriture. Ouvrir la boucle sur le seul label est un opt-in explicite,
`cursor.label_admits_untrusted_authors: true`.

`loop.codex_not_before` est une pause manuelle, absente du fichier livré : une
date committée éteint toute la boucle jusqu'à son échéance. Chaque cycle bloqué
par cette clé le journalise en avertissement. La pause automatique après quota
Codex, elle, vit dans l'état d'exécution (`codex_retry_after`).

## Cycle

Toutes les 30 minutes, un cycle :

1. contrôle les PR déjà validées par Claude et fusionne celles dont la CI est
   devenue verte ;
2. reprend une correction demandée ou une tâche prête ;
3. à défaut, sélectionne une PR Cursor, puis une Issue `agent-ready` ;
4. crée ou reprend un worktree `.jarvis/engineering-team/worktrees/<task>` ;
5. demande à Codex d'auditer le changement existant ou d'implémenter le correctif ;
6. exécute jusqu'à trois commandes de test parsées sans shell ;
7. offre une passe de réparation Codex si les tests échouent ;
8. commit, push et crée/met à jour une PR draft ;
9. fait relire le SHA exact par Claude ;
10. si Claude demande des changements, renvoie la tâche à Codex ;
11. si Claude valide, attend les checks GitHub ;
12. si un check échoue, renvoie la tâche à Codex ;
13. si le SHA change, invalide la revue Claude et la rejoue ;
14. si tous les checks sont verts, convertit la PR en ready puis la fusionne en
    `squash`.

Un verrou fichier empêche deux cycles simultanés. Une phase laissée en cours par
un arrêt brutal est automatiquement replacée dans la file au cycle suivant.
Chaque appel Codex est borné à 15 minutes et chaque revue Claude à 10 minutes.
Cinq tentatives de correction infructueuses bloquent la tâche et déclenchent une
alerte au lieu de contourner les contrôles.

Quand l'abonnement Codex signale une limite d'usage, la file est conservée sans
consommer de tentative et reprend après l'heure de renouvellement détectée ou
configurée.

## Portes d'auto-fusion

Le merge est autorisé seulement si toutes les conditions suivantes sont vraies :

- PR ouverte vers `main` ;
- PR créée par `app/cursor` ou branche gérée `codex/jarvis/*` ;
- tests locaux obligatoires réussis ;
- verdict Claude `approve` sans finding bloquant ;
- SHA GitHub identique au SHA lu par Claude ;
- au moins un check CI publié ;
- tous les checks terminés en `SUCCESS`, `NEUTRAL` ou `SKIPPED` ;
- aucun conflit Git.

Le runner n'utilise jamais `--admin`, ne force-push pas, ne modifie pas la
protection de branche et ne fusionne aucune PR humaine hors de cette allowlist.

## Commandes

```bash
venv/bin/python scripts/engineering_team.py doctor
venv/bin/python scripts/engineering_team.py status
venv/bin/python scripts/engineering_team.py enqueue \
  --title "Titre" \
  --request "Description complète" \
  --acceptance "Comportement vérifiable" \
  --test "python -m pytest tests/test_cible.py -q"
venv/bin/python scripts/engineering_team.py cycle --json
```

## Routage Slack

- `#proj-jarvis-roadmap` : intake Cursor et sélection du backlog.
- `#proj-jarvis-agents` : audit Codex, correctifs, PR et merges.
- `#proj-jarvis-ci` : tests locaux et checks GitHub.
- `#proj-jarvis-reviews` : verdicts Claude et invalidations de SHA.
- `#proj-jarvis-security` : vulnérabilités et Issue GitHub associée.
- `#proj-jarvis-hub` : synthèse des progrès, blocages et merges.

Le runner produit des événements JSON sans jeton Slack. L'automatisation Codex,
authentifiée au connecteur Slack, crée les handoffs GitHub manquants et route les
événements.

## Fichiers et état

- Configuration : `Architecture/engineering-team.json`
- Orchestrateur : `agents/engineering_team/workflow.py`
- Prompts : `prompts/engineering_team/`
- État runtime : `.jarvis/engineering-team/state.json`
- Worktrees : `.jarvis/engineering-team/worktrees/`
