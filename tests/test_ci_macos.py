"""Contrat structurel du job GitHub Actions exécuté sur macOS."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _macos_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  macos_smoke:")
    end = workflow.index("\n  backend:", start)
    return workflow[start:end]


def test_ci_has_a_real_macos_14_job():
    job = _macos_job()

    assert "runs-on: macos-14" in job
    assert "python -m pip install -r requirements-dev.txt" in job
    assert "python -m pip check" in job
    assert "brew install portaudio libsndfile" in job


def test_macos_job_runs_native_and_simulated_apple_contracts():
    job = _macos_job()

    for test_file in (
        "test_macos_runtime.py",
        "test_apple_data.py",
        "test_calendar_no_foreground.py",
        "test_imessage_import.py",
        "test_audio_defaults.py",
        "test_native_audio_pipeline.py",
        "test_screen_watcher_control.py",
    ):
        assert test_file in job
