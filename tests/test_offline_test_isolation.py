"""Contrat d'isolation réseau de la suite : ce qui doit rester vrai.

Trois choses se dégradent silencieusement avec le temps si personne ne les
verrouille : la désélection par défaut des tests réseau, la politique d'`skip`
(qui devient vite « ignore tout ce qui échoue »), et la séparation entre la CI
de pull request et la CI réseau. Ce fichier échoue si l'une des trois glisse.
"""

from __future__ import annotations

import asyncio
import configparser
import errno
import importlib
import socket
from pathlib import Path
from typing import Any, Final

import pytest

from audio.tts_errors import TTSFailureKind, classify_tts_failure

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PYTEST_INI: Final[Path] = PROJECT_ROOT / "pytest.ini"
CI_WORKFLOW: Final[Path] = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
EXTERNAL_WORKFLOW: Final[Path] = PROJECT_ROOT / ".github" / "workflows" / "external-network.yml"
README: Final[Path] = PROJECT_ROOT / "README.md"

EXTERNAL_MARKER: Final[str] = "external_network"
TTS_MARKER: Final[str] = "integration_tts"

root_conftest = importlib.import_module("conftest")
external_tests = importlib.import_module("tests.test_tts_edge_external")


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


def test_standard_suite_deselects_external_network_by_default():
    addopts = _pytest_ini()["addopts"]

    assert f'-m "not {EXTERNAL_MARKER}"' in addopts
    # Sans `--strict-markers`, une faute de frappe dans un marqueur passe
    # inaperçue et le test réseau se retrouve rejoué par défaut.
    assert "--strict-markers" in addopts


def test_external_tests_are_effectively_deselected(pytestconfig: pytest.Config):
    """Vérifie l'effet réel de la configuration sur la session courante."""
    assert pytestconfig.getoption("markexpr") == f"not {EXTERNAL_MARKER}"


def test_external_module_marks_every_test():
    assert external_tests.pytestmark == [
        pytest.mark.external_network,
        pytest.mark.integration_tts,
    ]


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
        ("speech.platform.bing.com", 443),
        ("93.184.216.34", 443),
        ("2606:2800:220:1:248:1893:25c8:1946", 443),
        ("api.deepseek.com", 443),
        ("192.168.1.10", 8080),
        (None, 443),
    ],
    ids=["edge_tts", "ipv4_public", "ipv6_public", "llm", "reseau_local", "hote_invalide"],
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
    # La classification TTS doit y voir une indisponibilité, pas un défaut.
    assert classify_tts_failure(blocked) is TTSFailureKind.NETWORK_UNAVAILABLE


def test_guard_refuses_a_real_outbound_connection(request: pytest.FixtureRequest):
    """Vérification de bout en bout : la connexion n'a pas lieu."""
    with pytest.raises(root_conftest.OutboundNetworkBlocked) as blocked:
        socket.create_connection(("speech.platform.bing.com", 443), timeout=1)

    assert "speech.platform.bing.com:443" in str(blocked.value)
    # Cette tentative est volontaire : on la retire du récapitulatif de session.
    assert root_conftest.drain_blocked_attempts(request.node.nodeid) == [
        "speech.platform.bing.com:443"
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


# ── 3. Politique d'skip du module réseau ─────────────────────────────────────


async def _raise(exc: BaseException) -> None:
    raise exc


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(61, "Connection refused"),
        TimeoutError("timed out"),
        OSError(errno.ENETUNREACH, "Network is unreachable"),
    ],
    ids=["refus_tcp", "timeout", "reseau_injoignable"],
)
async def test_external_helper_skips_only_identified_unavailability(exc: BaseException):
    assert classify_tts_failure(exc) is TTSFailureKind.NETWORK_UNAVAILABLE

    with pytest.raises(pytest.skip.Exception) as skipped:
        await external_tests._await_or_skip(_raise(exc), what="test")

    assert "Edge injoignable" in str(skipped.value)
    assert "network_unavailable" in str(skipped.value)


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("Invalid voice 'pas-une-voix'."),
        RuntimeError("réponse inattendue du service"),
        AssertionError("format audio invalide"),
    ],
    ids=["parametre_invalide", "reponse_inattendue", "format_invalide"],
)
async def test_external_helper_never_skips_a_functional_failure(exc: BaseException):
    assert classify_tts_failure(exc) is TTSFailureKind.FUNCTIONAL

    with pytest.raises(type(exc)):
        await external_tests._await_or_skip(_raise(exc), what="test")


async def test_external_helper_bounds_every_network_call():
    """Un appel réseau qui ne répond jamais ne doit pas figer la CI réseau."""

    async def _never() -> None:
        await asyncio.sleep(3600)

    external_timeout = external_tests.NETWORK_TIMEOUT_SEC
    assert 0 < external_timeout <= 120

    with pytest.raises(pytest.skip.Exception):
        await external_tests._await_or_skip(
            asyncio.wait_for(_never(), timeout=0.01), what="test"
        )


# ── 4. Séparation des pipelines CI et documentation ──────────────────────────


def test_pull_request_ci_never_runs_external_tests():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert EXTERNAL_MARKER not in workflow


def test_a_dedicated_workflow_runs_external_tests_on_demand():
    workflow = EXTERNAL_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert f"-m {EXTERNAL_MARKER}" in workflow
    assert "pull_request" not in workflow


def test_readme_documents_the_three_execution_modes():
    readme = README.read_text(encoding="utf-8")

    assert f"-m {EXTERNAL_MARKER}" in readme
    assert TTS_MARKER in readme
