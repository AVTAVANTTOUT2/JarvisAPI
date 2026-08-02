"""Dépôts git jetables et hermétiques pour les tests.

Un `git commit` hérite par défaut de la configuration de la machine :
identité de l'auteur, signature GPG, `core.hooksPath`, template de message.
Un test qui s'appuie dessus passe sur le poste d'un développeur configuré et
échoue ailleurs avec « Author identity unknown » (code 128), une demande de
passphrase GPG ou un hook maison — sans que le code testé soit en cause.

Ces helpers neutralisent les configurations globale et système
(`GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM`, git ≥ 2.32) et fournissent une
identité fixe par la ligne de commande. Rien n'est écrit hors de `path` :
la configuration git de la machine n'est jamais modifiée.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Identité et garde-fous injectés à chaque invocation. `-c` ne persiste rien.
_GIT_OVERRIDES: tuple[str, ...] = (
    "-c", "user.name=JARVIS Tests",
    "-c", "user.email=tests@jarvis.local",
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "init.defaultBranch=main",
    "-c", "advice.detachedHead=false",
)

DEFAULT_BRANCH = "main"
INITIAL_COMMIT_MESSAGE = "init"


def _hermetic_env() -> dict[str, str]:
    """Environnement enfant coupé des configurations git de la machine."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        # Aucun test ne doit pouvoir bloquer sur une invite d'identifiants.
        # (`GIT_ASKPASS` n'est pas défini à vide : git tenterait de l'exécuter.)
        "GIT_TERMINAL_PROMPT": "0",
    }


def git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Lance `git` dans `repo`, isolé de la configuration de la machine.

    Args:
        repo: Répertoire de travail du dépôt.
        args: Arguments passés à git, sans le nom du binaire.
        check: Lève `CalledProcessError` si le code de retour n'est pas nul.

    Returns:
        Le processus terminé, `stdout` et `stderr` capturés en texte.

    Raises:
        subprocess.CalledProcessError: Si `check` et que git échoue. Le message
            d'erreur inclut la commande, le dépôt et la sortie d'erreur.
    """
    result = subprocess.run(
        ["git", *_GIT_OVERRIDES, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_hermetic_env(),
        check=False,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            f"git {' '.join(args)} (dans {repo})",
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


#: Réglages écrits dans `.git/config` du dépôt jetable. Indispensables : le
#: code testé (self-healing, délégation Cursor) lance ses propres `git commit`
#: dans son environnement, sans hériter des `-c` ci-dessus. Sans identité
#: locale, ces commits échouent en 128 sur une machine non configurée.
_LOCAL_CONFIG: tuple[tuple[str, str], ...] = (
    ("user.name", "JARVIS Tests"),
    ("user.email", "tests@jarvis.local"),
    ("commit.gpgsign", "false"),
    ("tag.gpgsign", "false"),
)


def init_repo(path: Path, *, branch: str = DEFAULT_BRANCH) -> Path:
    """Initialise un dépôt vide et autonome dans `path`, sans commit.

    Args:
        path: Répertoire du dépôt, créé s'il manque.
        branch: Nom de la branche initiale, fixé pour ne pas dépendre de la
            valeur par défaut de la version de git installée.

    Returns:
        `path`, pour permettre l'enchaînement.
    """
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    for key, value in _LOCAL_CONFIG:
        git(path, "config", key, value)
    return path


def commit_all(repo: Path, message: str = INITIAL_COMMIT_MESSAGE) -> None:
    """Indexe tout l'arbre de travail et crée un commit.

    Args:
        repo: Dépôt déjà initialisé.
        message: Message du commit.
    """
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def init_repo_with_commit(
    path: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    message: str = INITIAL_COMMIT_MESSAGE,
) -> Path:
    """Initialise un dépôt et y crée un premier commit de tout le contenu.

    Args:
        path: Répertoire du dépôt, créé s'il manque. Son contenu existant est
            inclus dans le commit initial.
        branch: Nom de la branche initiale.
        message: Message du commit initial.

    Returns:
        `path`, pour permettre l'enchaînement.
    """
    init_repo(path, branch=branch)
    commit_all(repo=path, message=message)
    return path
