# Dépendances Python reproductibles

Les fichiers `requirements*.txt` à la racine sont les sources lisibles par un
humain. Une installation ou une CI ne doit pas les résoudre directement : les
locks Python 3.12 dans `requirements/locks/` fixent chaque version et chaque
artefact autorisé par un hash SHA-256.

## Profils

| Profil | Cible | Lock |
|---|---|---|
| `production-linux` | serveur/CI Ubuntu x86_64 | `production-linux-x86_64-py312.txt` |
| `production-macos` | Mac Apple Silicon | `production-macos-arm64-py312.txt` |
| `ci-linux` | tests backend légers Ubuntu | `ci-linux-x86_64-py312.txt` |
| `dev-macos` | tests + production sur Mac Apple Silicon | `dev-macos-arm64-py312.txt` |
| `agent-macos` | agent distant Mac | `agent-macos-arm64-py312.txt` |
| `mlx-macos` | sidecar MLX audio, Apple Silicon/macOS 14+ | `mlx-macos-arm64-py312.txt` |

Installation type :

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --require-hashes \
  -r requirements/locks/production-macos-arm64-py312.txt
```

## Mise à jour contrôlée

Le générateur exige exactement `uv 0.11.29`. Il conserve les versions déjà
résolues par défaut et refuse un lock sans version exacte ou sans hash.
Le profil MLX doit être régénéré sur un Mac Apple Silicon sous macOS 14 ou plus
récent, seuil minimal des roues MLX actuellement résolues.

```bash
# Vérification locale, sans accès réseau
python tools/update_python_locks.py --check

# Recalcul après modification d'une source
python tools/update_python_locks.py

# Mise à jour volontaire d'un paquet seulement
python tools/update_python_locks.py --upgrade-package fastapi

# Montée globale explicite (à isoler dans une PR dédiée)
python tools/update_python_locks.py --upgrade
```

Après génération, exécuter les installations `--require-hashes`, `pip check`,
les imports de fumée et les tests des plateformes concernées. Une modification
d'un fichier source invalide son digest dans le lock et fait échouer la CI tant
qu'il n'a pas été régénéré.
