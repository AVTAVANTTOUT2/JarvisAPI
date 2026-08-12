# Claw3D — UI visuelle optionnelle de JARVIS

Claw3D fournit le bureau isométrique, les acteurs visuels et le builder Phaser de
JARVIS. Son installation est **facultative** : le backend, les applications
existantes et les health-checks JARVIS ne dépendent jamais de Claw3D.

Le gestionnaire versionné [`scripts/claw3d.py`](../scripts/claw3d.py) rend le
déploiement reproductible sans incorporer le code Claw3D au runtime Python. Le
dépôt Claw3D complet, son `.git`, `node_modules`, ses builds, caches, logs, PID et
configuration restent regroupés dans :

```text
JarvisAPI/.jarvis/apps/claw3d/
```

Ce chemin est ignoré par Git. Le supprimer n'enlève aucun fichier source ou
donnée JARVIS.

## Version déployée

Le gestionnaire n'accepte pas d'URL arbitraire. Il épingle exactement :

- dépôt : `https://github.com/AVTAVANTTOUT2/Claw3D.git` ;
- commit : `202feaf0efd8ae92451368d408e387a507da0192`.

Une mise à jour de Claw3D doit modifier ce couple, adapter les tests puis passer
par une nouvelle PR JarvisAPI. Il n'existe aucun `git pull` automatique.

## Prérequis

- une installation JarvisAPI clonée normalement ;
- Git ;
- Node.js 24.19.0 ou plus récent ;
- npm 11.17.0 ou plus récent ;
- accès réseau uniquement pendant le clone et `npm ci` initial.

Aucun `sudo`, package npm global, LaunchAgent, daemon ou service système n'est
installé par ce parcours.

## Installation autonome en mode mock

Le mode mock est recommandé pour valider l'UI sans démarrer JARVIS :

```bash
python3 scripts/claw3d.py install --mode mock
python3 scripts/claw3d.py start
python3 scripts/claw3d.py status
```

Ouvrir ensuite :

- `http://127.0.0.1:3000/office` ;
- `http://127.0.0.1:3000/office/builder`.

Le mode mock ne contacte jamais JARVIS.

## Connexion en lecture seule à JARVIS

Quand `CLAW3D_MANAGED_BY_SUPERVISOR=true` (défaut), le **superviseur** démarre et
arrête Claw3D avec JARVIS. Prérequis unique : une installation préalable.

```bash
# Une seule fois
python3 scripts/claw3d.py install --mode jarvis-readonly \
  --jarvis-origin https://127.0.0.1:8081

# Ensuite : tout démarre/arrête avec le superviseur
./scripts/launch_supervisor.sh
# ou LaunchAgent com.jarvis.supervisor
```

Le superviseur réécrit la config Claw3D à chaque démarrage pour coller à
l'origine réelle du backend (`http(s)://127.0.0.1:$WEB_PORT`). Si Claw3D n'est
pas installé, JARVIS démarre normalement et ignore l'UI visuelle.
Si `CLAW3D_PORT` appartient déjà à un processus qui n'est pas identifié par
l'état Claw3D, le superviseur signale `service_port_conflict` et ne démarre ni
n'arrête ce processus tiers. Il faut libérer le port ou choisir un autre port.

Pilote manuel (équivalent) :

```bash
python3 scripts/claw3d.py configure \
  --mode jarvis-readonly \
  --jarvis-origin https://127.0.0.1:8081
python3 scripts/claw3d.py stop
python3 scripts/claw3d.py start
```

Désactiver le couplage superviseur sans retirer Claw3D :

```bash
# dans .env / .env.config
CLAW3D_MANAGED_BY_SUPERVISOR=false
```

Le connecteur Claw3D est côté serveur et n'utilise que :

- `GET /api/status` ;
- `GET /api/events/stream`.

Le navigateur reçoit uniquement le contrat visuel neutralisé via les routes
same-origin Claw3D. Aucun token, cookie, prompt, contenu de conversation,
argument/résultat d'outil ou payload brut JARVIS n'est copié dans le bundle,
les fixtures, les logs ou le stockage navigateur.

L'authentification de session n'est pas relayée. Si JARVIS exige un cookie
`SameSite=Strict` non partageable entre origines, Claw3D reste verrouillé au lieu
d'affaiblir le cookie ou d'exposer un secret.

## Désactivation immédiate

Cette procédure ne modifie aucune configuration JARVIS :

```bash
python3 scripts/claw3d.py configure --mode null
python3 scripts/claw3d.py stop
python3 scripts/claw3d.py start
```

Le mode `null` affiche l'état hors ligne et n'émet aucune requête JARVIS.

## Vérification, arrêt et nettoyage

```bash
python3 scripts/claw3d.py verify
python3 scripts/claw3d.py stop
python3 scripts/claw3d.py clean --dry-run
python3 scripts/claw3d.py clean
```

`clean` délègue au script Claw3D et retire uniquement ses artefacts
régénérables. Le code source, `.git` et `.env` Claw3D sont conservés.

Les commandes sont idempotentes : un deuxième install réutilise le checkout et
le cache npm si le lockfile est inchangé ; un deuxième start/stop produit un
état prévisible.

## Suppression complète

Avant la suppression, si la persistance navigateur a été activée, utiliser dans
l'UI l'action **Erase browser preferences**. Aucun script shell ne peut effacer
à distance les caches génériques du navigateur.

La suppression du checkout complet est une action interactive distincte :

```bash
python3 scripts/claw3d.py remove-source
```

JarvisAPI délègue intégralement cette opération à la commande
`scripts/uninstall.sh --remove-source` dans Claw3D. Le script affiche la cible
canonique, refuse les liens symboliques, `/` et le home, exige une confirmation
exacte et utilise la corbeille lorsqu'une commande `trash` existe.

Cette option ne doit jamais être lancée en CI. Déplacer ou supprimer le seul
répertoire `.jarvis/apps/claw3d` après arrêt suffit à retirer l'UI et son état
propre. Cela ne constitue pas une garantie d'effacement forensique des caches
génériques du système, du navigateur, de GitHub ou des outils.

## Invariants d'indépendance

- aucun import Python/TypeScript croisé ;
- aucune dépendance `file:`, workspace, sous-module ou source vendored ;
- aucun accès Claw3D à SQLite, `data/`, `.env` ou aux secrets JARVIS ;
- aucun volume partagé ;
- démarrage optionnel via le **superviseur** uniquement (`CLAW3D_MANAGED_BY_SUPERVISOR`),
  jamais via `main.py` ni un LaunchAgent Claw3D dédié ;
- absence ou panne de Claw3D : JARVIS continue sans UI visuelle ;
- arrêter ou retirer Claw3D ne change pas le fonctionnement de JARVIS ;
- avec `CLAW3D_MANAGED_BY_SUPERVISOR=false`, le mode mock et le builder restent
  utilisables indépendamment de JARVIS.

La décision d'architecture est consignée dans
[`ADR-027`](../Architecture/adr/ADR-027-claw3d-ui-optionnelle.md).
