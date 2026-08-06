"""Le plan de contrôle est joignable en même origine, et seulement là.

Ce fichier verrouille une panne qui a duré plusieurs jours sans être comprise.

`/api/supervisor/*` et `/ws/supervisor` n'existent que dans le processus
superviseur, et le serveur y impose ``Origin == Host``. Or le backend sert la
**même** page `/control`. Ouverte depuis le backend, l'interface appelait des
routes absentes, ouvrait un WebSocket fermé en 4403, et traduisait le tout par
« Superviseur inaccessible — démarrez-le » — alors qu'il tournait.

Trois propriétés sont donc figées ici :

1. le backend n'expose aucune route de plan de contrôle (sinon le diagnostic
   redeviendrait ambigu) ;
2. le frontend ne fabrique jamais d'URL superviseur hors même origine ;
3. le port du superviseur a une source unique côté Python et une seule valeur
   côté TypeScript, les deux alignées.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_TS = PROJECT_ROOT / "frontend" / "src" / "lib" / "api.ts"


def test_backend_exposes_no_supervisor_route():
    """Le plan de contrôle n'appartient pas au backend.

    S'il s'y invitait, une page servie par le backend piloterait les services
    par un chemin que le superviseur ne connaît pas — et les deux vérités
    diverger aient en silence.
    """
    routers = sorted((PROJECT_ROOT / "api").glob("router_*.py"))
    assert routers, "aucun routeur trouvé : le test ne vérifie plus rien"

    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}:{number}"
        for path in routers
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "/api/supervisor" in line or "/ws/supervisor" in line
    ]
    assert offenders == [], f"route de plan de contrôle côté backend : {offenders}"


def test_supervisor_port_has_a_single_python_source():
    """`supervisor.py` lit la configuration au lieu de relire l'environnement."""
    source = (PROJECT_ROOT / "supervisor.py").read_text(encoding="utf-8")
    assert "SUPERVISOR_PORT = config.SUPERVISOR_PORT" in source
    assert 'os.getenv("SUPERVISOR_PORT"' not in source
    assert isinstance(config.SUPERVISOR_PORT, int) and config.SUPERVISOR_PORT > 0


def test_frontend_port_matches_configuration():
    """Une divergence de port ne se verrait qu'à l'usage, trop tard.

    Le frontend ne peut pas lire `config.py` au build ; la seule protection est
    de comparer les deux constantes ici.
    """
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(r"export const SUPERVISOR_PORT = (\d+)", source)
    assert match, "SUPERVISOR_PORT introuvable dans frontend/src/lib/api.ts"
    assert int(match.group(1)) == config.SUPERVISOR_PORT


def test_frontend_never_builds_a_cross_origin_supervisor_url():
    """Aucune URL superviseur ne doit viser un autre hôte ou un autre port.

    Le serveur ferme ces connexions en 4403. Une URL qui « cible » le port du
    superviseur depuis une autre origine ne se répare pas côté client : elle
    produit un échec que l'interface confond avec une panne.
    """
    source = API_TS.read_text(encoding="utf-8")

    # On vérifie la fonction qui **ouvre** la connexion, pas celle qui affiche
    # un lien. `supervisorOrigin()` construit légitimement une origine distante :
    # c'est du texte destiné à l'utilisateur, jamais une requête.
    match = re.search(
        r"export function supervisorWsUrl\(\)[^{]*\{(.*?)\n\}", source, re.S
    )
    assert match, "supervisorWsUrl introuvable"
    body = match.group(1)

    assert "window.location.host" in body, (
        "supervisorWsUrl doit se dériver de l'hôte courant"
    )
    assert "hostname" not in body, (
        "supervisorWsUrl reconstruit une origine : elle serait fermée en 4403"
    )
    assert "SUPERVISOR_PORT" not in body.replace(
        "ws://127.0.0.1:${SUPERVISOR_PORT}", ""
    ), "supervisorWsUrl impose un port : ce n'est plus la même origine"


@pytest.mark.parametrize("helper", ["isServedBySupervisor", "supervisorOrigin"])
def test_control_view_states_are_distinguished(helper: str):
    """L'interface sépare « mauvaise origine » de « superviseur arrêté ».

    Les deux appellent des gestes opposés : changer d'adresse, ou démarrer un
    processus. Les confondre a envoyé l'utilisateur relancer pendant des jours
    un superviseur qui fonctionnait.
    """
    view = PROJECT_ROOT / "web" / "src" / "app" / "components" / "views" / "ControlView.tsx"
    source = view.read_text(encoding="utf-8")
    assert helper in source, f"{helper} n'est plus utilisé par ControlView"
