"""Contrats de découverte de la suite pytest canonique."""

from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TEST_PATHS = ("tests", "jarvis/tests", "agents/devagent")
STANDALONE_DIAGNOSTICS = (
    ROOT / "scripts" / "test_macos_permissions.py",
    ROOT / "scripts" / "test_screen_capture.py",
    ROOT / "scripts" / "test_voice_pipeline.py",
)


def test_implicit_pytest_collection_is_limited_to_the_canonical_suite():
    parser = ConfigParser()
    parser.read(ROOT / "pytest.ini", encoding="utf-8")

    configured = tuple(parser["pytest"]["testpaths"].split())
    assert configured == CANONICAL_TEST_PATHS


def test_hardware_diagnostics_explicitly_opt_out_of_pytest_collection():
    for script in STANDALONE_DIAGNOSTICS:
        source = script.read_text(encoding="utf-8")
        assert "__test__ = False" in source, script
