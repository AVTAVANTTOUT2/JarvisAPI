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
    assert "python -m pip install --require-hashes" in job
    assert "requirements/locks/production-linux-x86_64-py312.txt" in job
    assert "python tools/update_python_locks.py --check" in job
    assert "python -m pip check" in job
    assert "portaudio19-dev" in job


def test_ci_smoke_imports_risky_production_dependencies():
    job = _production_job()

    for module in (
        "faster_whisper",
        "pyaudio",
        "sentence_transformers",
        "spacy",
        "torch",
        "torchaudio",
    ):
        assert f'"{module}"' in job


def test_production_requirements_keep_spacy_numpy_compatible():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "numpy>=2.0.2,<3" in requirements
    assert "spacy==3.8.*" in requirements


def test_production_requirements_carry_no_speech_engine():
    """La synthèse tourne dans le venv MLX, pas dans les dépendances du serveur.

    Une dépendance vocale ici la rendrait obligatoire à l'installation — et,
    pour Edge, réintroduirait une sortie réseau.
    """
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

    for forbidden in ("edge-tts", "kokoro-onnx", "piper", "xtts", "coqui"):
        assert forbidden not in requirements


def test_production_requirements_pin_the_web_core():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "fastapi==0.139.*" in requirements
    assert "uvicorn[standard]==0.34.*" in requirements
