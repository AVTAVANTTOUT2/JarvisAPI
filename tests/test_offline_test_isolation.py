"""Contrat d'isolation réseau de la suite : ce qui doit rester vrai.

Trois choses se dégradent silencieusement avec le temps si personne ne les
verrouille : la désélection par défaut des tests réseau, la politique d'`skip`
(qui devient vite « ignore tout ce qui échoue »), et la séparation entre la CI
de pull request et la CI réseau. Ce fichier échoue si l'une des trois glisse.
"""

from __future__ import annotations

import configparser
import errno
import importlib
import socket
from pathlib import Path
from typing import Any, Final

import pytest
from dataclasses import replace

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PYTEST_INI: Final[Path] = PROJECT_ROOT / "pytest.ini"
CI_WORKFLOW: Final[Path] = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
EXTERNAL_WORKFLOW: Final[Path] = PROJECT_ROOT / ".github" / "workflows" / "external-network.yml"
README: Final[Path] = PROJECT_ROOT / "README.md"

EXTERNAL_MARKER: Final[str] = "external_network"
TTS_MARKER: Final[str] = "integration_tts"

root_conftest = importlib.import_module("conftest")


def _pytest_ini() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(PYTEST_INI, encoding="utf-8")
    return parser["pytest"]


# ── 1. Configuration pytest ──────────────────────────────────────────────────


@pytest.mark.parametrize("marker", [EXTERNAL_MARKER, TTS_MARKER])
def test_markers_are_declared_with_a_description(marker: str):
    declared = _pytest_ini()["markers"]

    lines = [line for line in declared.splitlines() if line.strip().startswith(f"{marker}:")]
    assert lines, f"marqueur `{marker}` non déclaré dans pytest.ini"
    assert len(lines[0].split(":", 1)[1].strip()) > 20, "description de marqueur trop vague"


def test_standard_suite_deselects_external_and_hardware_tests_by_default():
    addopts = _pytest_ini()["addopts"]

    assert f"not {EXTERNAL_MARKER}" in addopts
    assert f"not {TTS_MARKER}" in addopts
    # Sans `--strict-markers`, une faute de frappe dans un marqueur passe
    # inaperçue et le test réseau se retrouve rejoué par défaut.
    assert "--strict-markers" in addopts


def test_explicit_integrations_are_effectively_deselected(pytestconfig: pytest.Config):
    """Vérifie l'effet réel de la configuration sur la session courante."""
    markexpr = pytestconfig.getoption("markexpr")
    assert f"not {EXTERNAL_MARKER}" in markexpr
    assert f"not {TTS_MARKER}" in markexpr


def test_no_test_reaches_the_network_without_the_marker():
    """La pile vocale étant locale, plus aucun test n'a de raison de sortir.

    Le marqueur reste déclaré : c'est lui qui rend visible, et volontaire,
    toute future dépendance réseau d'un test.
    """
    marked = [
        path
        for path in (PROJECT_ROOT / "tests").glob("test_*.py")
        if f"pytest.mark.{EXTERNAL_MARKER}" in path.read_text(encoding="utf-8")
        and path.name != "test_offline_test_isolation.py"
    ]
    assert marked == []


# ── 2. Garde-fou de connexions sortantes ─────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        ("127.0.0.1", 8080),
        ("::1", 8080),
        ("[::1]", 8080),
        ("::1%lo0", 8080),
        ("localhost", 5432),
        ("127.255.255.254", 1),
        (b"localhost", 1),
        "/tmp/jarvis.sock",
        b"/tmp/jarvis.sock",
        (),
    ],
    ids=[
        "ipv4_loopback",
        "ipv6_loopback",
        "ipv6_crochets",
        "ipv6_zone",
        "nom_local",
        "loopback_haut",
        "hote_en_octets",
        "socket_unix",
        "socket_unix_octets",
        "adresse_vide",
    ],
)
def test_loopback_addresses_stay_allowed(address: Any):
    assert root_conftest._is_loopback_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        ("huggingface.co", 443),
        ("93.184.216.34", 443),
        ("2606:2800:220:1:248:1893:25c8:1946", 443),
        ("api.deepseek.com", 443),
        ("192.168.1.10", 8080),
        (None, 443),
    ],
    ids=["poids_hf", "ipv4_public", "ipv6_public", "llm", "reseau_local", "hote_invalide"],
)
def test_outbound_addresses_are_blocked(address: Any):
    assert root_conftest._is_loopback_address(address) is False


def test_blocked_connection_looks_exactly_like_being_offline():
    """Le refus doit imiter une machine sans réseau, pas inventer une panne.

    Les replis hors ligne déjà écrits (torch.hub, aiohttp, requests) attrapent
    `OSError` : ils doivent continuer à fonctionner sous le garde-fou.
    """
    blocked = root_conftest.OutboundNetworkBlocked("cible refusée")

    assert isinstance(blocked, ConnectionError)
    assert blocked.errno == errno.ECONNREFUSED


def test_guard_refuses_a_real_outbound_connection(request: pytest.FixtureRequest):
    """Vérification de bout en bout : la connexion n'a pas lieu."""
    with pytest.raises(root_conftest.OutboundNetworkBlocked) as blocked:
        socket.create_connection(("huggingface.co", 443), timeout=1)

    assert "huggingface.co:443" in str(blocked.value)
    # Cette tentative est volontaire : on la retire du récapitulatif de session.
    assert root_conftest.drain_blocked_attempts(request.node.nodeid) == [
        "huggingface.co:443"
    ]


def test_guard_still_allows_loopback_connections():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            assert client.getpeername()[1] == port


def test_network_guard_is_autouse_and_skips_external_tests():
    fixture = root_conftest._block_outbound_network
    # pytest 8.4 expose `_fixture_function_marker` ; les versions antérieures
    # `_pytestfixturefunction`. Les deux portent le drapeau `autouse`.
    marker = getattr(fixture, "_fixture_function_marker", None) or getattr(
        fixture, "_pytestfixturefunction", None
    )

    assert marker is not None, "fixture non déclarée via @pytest.fixture"
    assert marker.autouse is True
    source = Path(root_conftest.__file__).read_text(encoding="utf-8")
    assert f'get_closest_marker("{EXTERNAL_MARKER}")' in source


# ── 3. La synthèse vocale ne sort pas de la machine ──────────────────────────


def test_local_tts_stack_declares_no_network_dependency():
    """Le fournisseur vocal doit se déclarer hors ligne, sans exception.

    C'est la propriété qui rend le test réseau inutile : il n'y a plus de
    moteur distant à joindre, donc plus de pipeline CI à lui consacrer.
    """
    from jarvis.audio.tts import create_local_tts_provider, load_tts_settings
    from jarvis.audio.tts.config import DEFAULT_TTS_PROVIDER

    settings = replace(load_tts_settings(), provider=DEFAULT_TTS_PROVIDER)
    assert create_local_tts_provider(settings).info().offline is True


# ── 4. Séparation des pipelines CI et documentation ──────────────────────────


def test_pull_request_ci_never_runs_external_tests():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert EXTERNAL_MARKER not in workflow


def test_no_workflow_runs_network_tests_anymore():
    """Le workflow réseau dédié n'existe plus : il n'existait que pour Edge."""
    assert not EXTERNAL_WORKFLOW.exists()


def test_readme_documents_the_execution_modes():
    readme = README.read_text(encoding="utf-8")

    assert f'-m "not {EXTERNAL_MARKER}"' in readme or EXTERNAL_MARKER in readme
    assert TTS_MARKER in readme
