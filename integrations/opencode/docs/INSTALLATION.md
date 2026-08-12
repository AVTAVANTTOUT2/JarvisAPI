# Installation et mise à niveau

## Préconditions

- Python et les dépendances JARVIS installés ;
- plateforme explicitement supportée : macOS arm64/x64, Linux arm64/x64 ou
  Windows x64 ;
- accès HTTPS à la release GitHub officielle, ou archive locale pré-téléchargée ;
- clé du fournisseur de modèle choisie. L'adaptateur JARVIS ne transmet
  explicitement que `DEEPSEEK_API_KEY` ; aucune autre variable secrète n'est
  héritée implicitement.

Le binaire n'est jamais installé globalement. La première exécution agentique
peut déclencher l'installation paresseuse si la vérification échoue. Pour une
installation contrôlée, exécuter depuis la racine du worktree :

```bash
python -m integrations.opencode.scripts.manager print-version
python -m integrations.opencode.scripts.manager install
python -m integrations.opencode.scripts.manager verify
python -m integrations.opencode.scripts.manager smoke-test --workspace /chemin/absolu/vers/le/worktree
```

Chaque commande émet du JSON et n'affiche jamais le mot de passe éphémère.
`smoke-test` démarre puis arrête le serveur ; il ne requiert aucun appel de
modèle ni clé externe.

Pour une archive locale déjà obtenue :

```bash
python -m integrations.opencode.scripts.manager install \
  --archive /chemin/absolu/opencode-darwin-arm64.zip \
  --platform darwin-arm64
python -m integrations.opencode.scripts.manager verify
```

L'archive est refusée si son nom de plateforme, sa taille, son SHA-256, ses
membres ou la version exécutée ne correspondent pas au manifeste.

## Release épinglée et empreintes

La version admise est `1.18.16` (`v1.18.16`, publiée le
2026-08-10T06:07:08Z). L'autoupdate est désactivé.

| Plateforme | Archive | Octets | SHA-256 |
|---|---|---:|---|
| macOS arm64 | `opencode-darwin-arm64.zip` | 46 053 503 | `1e670c94341a374824dc6700b6f38b2cb6634baf3ca20e645084c33ce6639320` |
| macOS x64 | `opencode-darwin-x64.zip` | 48 254 566 | `4cfa1d11e665ffb83b68dbefc4cadee0559d008e7ab40c92d14fc371c8b13595` |
| Linux arm64 | `opencode-linux-arm64.tar.gz` | 60 189 672 | `4fdce5f9bc877d977304d71c0c90ad6e83efa381fe0edf0a61e6142a625e1c41` |
| Linux x64 | `opencode-linux-x64.tar.gz` | 60 379 356 | `286e07355df06738c1905955be15b7fbc10a7b12d931de9394a6f7597246750b` |
| Windows x64 | `opencode-windows-x64.zip` | 60 501 625 | `a60bf4d8019982b81dc0c3b91b6e226442cf2b73aca817599b68779ac053e3ff` |

La taille maximale téléchargée est 256 MiB et la taille extraite 512 MiB.
Les URL exactes et tailles décimales faisant foi sont dans
`release-manifest.json`.

## Configuration locale

Les valeurs modifiables sont écrites dans
`integrations/opencode/.runtime/config/manager.json` :

```bash
python -m integrations.opencode.scripts.manager configure \
  --username jarvis-opencode \
  --startup-timeout 20 \
  --shutdown-timeout 10 \
  --request-timeout 15
```

L'hôte n'est pas configurable : `127.0.0.1` est imposé. La configuration
durcie `config/opencode.json` est reprovisionnée au démarrage. Ne pas y placer
de secret : l'auth serveur est générée par processus et stockée dans l'état
privé.

## Procédure de mise à niveau

Une mise à niveau est une revue de code, jamais un téléchargement automatique :

1. arrêter le processus avec `python -m integrations.opencode.scripts.manager stop` ;
2. examiner la release, les advisories et le diff du contrat OpenAPI amont ;
3. mettre à jour ensemble `release-manifest.json`, les constantes épinglées de
   `lifecycle/release.py`, `plugin.json`, le contrat sous `client/openapi/`, la
   licence/notice et cette documentation ;
4. renseigner toutes les URL, tailles et empreintes indépendamment vérifiées ;
5. lancer :

```bash
python -m pytest integrations/opencode/tests -q
python -m ruff check integrations/opencode
python -m integrations.opencode.scripts.manager install
python -m integrations.opencode.scripts.manager verify
python -m integrations.opencode.scripts.manager smoke-test --workspace /chemin/absolu/vers/le/worktree
```

6. exécuter ensuite les portes de qualité complètes JARVIS. Ne relever
   `minimum_safe_version` qu'après revue des bornes sémantiques de tous les avis.

Rollback : arrêter, restaurer l'ancien ensemble manifeste/code/contrat, puis
réinstaller. Une archive plus ancienne ne sera pas acceptée tant que le
manifeste actif pointe vers une autre version.
