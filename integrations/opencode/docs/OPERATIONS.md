# Exploitation et dépannage

## Commandes de référence

Depuis la racine du worktree :

```bash
python -m integrations.opencode.scripts.manager status
python -m integrations.opencode.scripts.manager health
python -m integrations.opencode.scripts.manager verify
python -m integrations.opencode.scripts.manager start --workspace /chemin/absolu/vers/le/worktree
python -m integrations.opencode.scripts.manager stop
python -m integrations.opencode.scripts.manager restart --workspace /chemin/absolu/vers/le/worktree
python -m integrations.opencode.scripts.manager clean
```

`status` observe l'état local ; `health` vérifie le serveur avec son auth
privée ; `verify` vérifie manifeste, binaire, hash et version. `clean` exige un
serveur arrêté et ne supprime que cache, logs et temporaire. Le chemin du
workspace doit exister, être absolu/résolu et ne pas être un lien.

Le démarrage manuel sert au diagnostic. En usage normal, l'adaptateur installe
si nécessaire, redémarre avec la capacité MCP du run, puis dispose le serveur.
`--allow-env NAME` transmet seulement une variable déjà présente et
explicitement autorisée ; ne jamais l'utiliser pour contourner la liste
auditée de l'adaptateur.

## Fichiers utiles

- état installation : `.runtime/state/install.json` ;
- état processus : `.runtime/state/process.json` ;
- auth privée : `.runtime/state/server-auth.json` ;
- capacités/idempotence : `.runtime/state/capabilities/` ;
- configuration effective : `.runtime/config/opencode.json` ;
- logs privés : `.runtime/logs/server.stdout.log` et `server.stderr.log`.

Ne jamais copier `server-auth.json`, les fichiers de capacité ou les logs dans
un ticket. `status`, `health` et `debug_command` sont conçus pour ne pas
afficher le secret. Redacter également chemins personnels et contenu de
workspace avant partage.

## Diagnostic ordonné

### Runtime indisponible

1. `python -m integrations.opencode.scripts.manager status`
2. `python -m integrations.opencode.scripts.manager verify`
3. vérifier que la plateforme figure dans `release-manifest.json` ;
4. réinstaller avec `manager install`, puis lancer `smoke-test`.

Un SHA invalide ne doit jamais être contourné avec `--skip-binary-check` : cette
option cachée ne sert qu'aux fixtures tests. Obtenir une archive officielle et
mettre à jour le manifeste par revue si la release change.

### Démarrage ou health-check expiré

Vérifier les deux logs privés, l'espace disque et qu'aucun contrôle local ne
bloque les sockets loopback. Le port est volontairement dynamique. Ne pas
ouvrir un port fixe/public. Après un échec, relancer `status` : les états morts
sont nettoyés, tandis qu'un PID vivant non attribuable provoque un refus sûr.
Ne pas tuer ce PID depuis le manager sans preuve ; l'identifier manuellement.

### Refus de propriété du processus

Le PID, le chemin du binaire, l'instance ou le health authentifié ne concorde
pas. Préserver l'état pour analyse. Si le processus appartient bien à cette
instance, arrêter le service depuis son propriétaire ; sinon, ne pas élargir
la primitive d'arrêt et ne pas supprimer l'état à l'aveugle.

### Contrat HTTP incompatible

`verify_contract` exige la version 1.18.16 et les endpoints décrits dans
`client/openapi/contract-v1.18.16.json`. Une incompatibilité signifie binaire,
contrat ou plugin désynchronisé. Arrêter et suivre la procédure d'upgrade ; ne
pas ajouter de fallback silencieux.

### Agents JARVIS manquants

La configuration effective doit contenir les quatre agents obligatoires.
Arrêter puis redémarrer : `config/opencode.json` est reprovisionné. Si le
problème persiste, comparer ce template au schéma de la version épinglée.

### Aucun modèle connecté

Le runtime est sain mais aucun provider n'annonce de modèle par défaut.
Configurer la clé autorisée sans la journaliser, puis redémarrer. L'intégration
transmet aujourd'hui uniquement `DEEPSEEK_API_KEY`. Ajouter un autre secret
demande une modification explicite de l'allowlist et une revue sécurité.

### Flux SSE interrompu

Le client reconnecte avec backoff borné et réconcilie la session. Après six
tentatives ou 90 secondes sans lecture, JARVIS publie un échec générique
`event_stream_interrupted` et signale qu'une attention est nécessaire. Vérifier
health et logs ; ne pas déclarer le run réussi sans événement et vérification.

### Permission ou MCP refusé

Contrôler l'ID de run, l'expiration, le profil, le workspace, le scope et les
métadonnées anti-récursion. Une clé d'idempotence réutilisée avec un payload
différent est refusée. Ne pas éditer les enveloppes privées pour contourner le
contrôle ; recréer le run avec les permissions minimales correctes.

## Validation après incident

```bash
python -m pytest integrations/opencode/tests -q
python -m ruff check integrations/opencode
python -m integrations.opencode.scripts.manager verify
python -m integrations.opencode.scripts.manager smoke-test --workspace /chemin/absolu/vers/le/worktree
```

Le smoke test valide le vrai binaire et son API locale sans appeler de modèle.
Un test de modèle ou une tâche réelle exige une clé et peut envoyer du contenu
au fournisseur externe ; il doit être exécuté séparément et explicitement.
