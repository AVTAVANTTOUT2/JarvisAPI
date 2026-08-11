# ADR-023 — Déployer Claw3D comme UI visuelle optionnelle

- Statut : accepté
- Date : 2026-08-11

## Contexte

Claw3D fournit une représentation 3D/2D utile de l'activité JARVIS, mais son
runtime historique mélangeait rendu et opérations métier. L'inclure directement
dans le processus Python, le dépôt suivi ou les services JARVIS introduirait une
dépendance de disponibilité, un partage de secrets et un rollback couplé.

## Décision

JarvisAPI versionne uniquement un gestionnaire manuel,
`scripts/claw3d.py`, et sa documentation. Le gestionnaire installe une révision
Claw3D exacte dans `.jarvis/apps/claw3d`, répertoire ignoré par Git et entièrement
amovible.

Le code Claw3D reste un dépôt Git autonome avec son propre lockfile, ses scripts
lifecycle et tout son état. Aucun module JARVIS ne l'importe et aucun service ne
le démarre automatiquement.

L'intégration réseau est initiée par Claw3D et limitée à deux lectures JARVIS :
`GET /api/status` et `GET /api/events/stream`. L'origine est fournie
explicitement par l'opérateur. Aucun secret ou cookie n'est relayé.

## Conséquences

### Positives

- installation en une commande depuis un checkout JarvisAPI ;
- fonctionnement mock indépendant de JARVIS ;
- suppression/rollback limités à un seul sous-répertoire ;
- version Claw3D reproductible et révisable par PR ;
- aucune nouvelle dépendance Python ou JavaScript dans le runtime JARVIS.

### Coûts assumés

- Git, Node.js et npm restent des prérequis de l'UI optionnelle ;
- la première installation nécessite le réseau ;
- les mises à jour Claw3D sont explicites, jamais automatiques ;
- l'authentification cross-origin reste désactivée si elle ne peut pas être
  relayée sans affaiblissement.

## Alternatives rejetées

- **Copie/vendoring du code Claw3D dans JarvisAPI** : licences, historique et
  dépendances deviennent couplés.
- **Sous-module Git** : ajoute un état Git implicite et complexifie clone,
  installation et suppression.
- **Proxy générique JARVIS** : élargit inutilement la surface réseau et pourrait
  exposer commandes ou secrets.
- **Service launchd commun** : empêcherait la suppression manuelle complète de
  Claw3D et créerait une dépendance de cycle de vie.

## Rollback

1. `python3 scripts/claw3d.py configure --mode null` ;
2. `python3 scripts/claw3d.py stop` ;
3. `python3 scripts/claw3d.py clean --dry-run` puis `clean` ;
4. retirer interactivement le checkout avec `remove-source` si souhaité ;
5. revenir sur le commit JarvisAPI antérieur pour retirer uniquement le
   gestionnaire et la documentation.

JARVIS ne demande aucun redémarrage pour les étapes 1 à 4.
