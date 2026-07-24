"""Contrats du job CI qui valide l'installation de production."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _production_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  production_dependencies:")
    end = workflow.index("\n  macos_smoke:", start)
    return workflow[start:end]


def test_ci_installs_real_production_requirements():
    job = _production_job()

    assert "runs-on: ubuntu-latest" in job
    assert "python -m pip install -r requirements.txt" in job
    assert "python -m pip check" in job
    assert "portaudio19-dev" in job


def test_ci_smoke_imports_risky_production_dependencies():
    job = _production_job()

    for module in (
        "faster_whisper",
        "interpreter",
        "kokoro_onnx",
        "pyaudio",
        "sentence_transformers",
        "spacy",
        "torch",
        "torchaudio",
    ):
        assert f'"{module}"' in job


def test_production_requirements_keep_spacy_and_kokoro_numpy_compatible():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "kokoro-onnx>=0.4" in requirements
    assert "numpy>=2.0.2,<3" in requirements
    assert "spacy==3.8.*" in requirements


def test_production_requirements_keep_pkg_resources_for_open_interpreter():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "open-interpreter==0.4.*" in requirements
    assert "setuptools>=77.0.3,<82" in requirements
