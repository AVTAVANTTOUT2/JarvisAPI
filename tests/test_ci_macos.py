"""Contrat structurel du job GitHub Actions exécuté sur macOS."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _macos_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  macos_smoke:")
    end = workflow.index("\n  backend:", start)
    return workflow[start:end]


def test_ci_has_a_real_macos_26_job():
    job = _macos_job()

    assert "runs-on: macos-26" in job
    assert "python -m pip install --require-hashes" in job
    assert "requirements/locks/dev-macos-arm64-py312.txt" in job
    assert "python -m pip check" in job
    assert "brew install xcodegen portaudio libsndfile" in job


def test_macos_job_runs_native_and_simulated_apple_contracts():
    job = _macos_job()

    for test_file in (
        "test_macos_runtime.py",
        "test_apple_data.py",
        "test_calendar_no_foreground.py",
        "test_imessage_consumer_cursor.py",
        "test_imessage_import.py",
        "test_imessage_sourcing.py",
        "test_audio_defaults.py",
        "test_native_audio_pipeline.py",
        "test_local_tts.py",
        "test_tts_segmenter.py",
        "test_screen_watcher_control.py",
    ):
        assert test_file in job


def test_macos_job_regenerates_and_builds_app_with_widget():
    job = _macos_job()

    assert "xcodegen generate" in job
    assert "git diff --exit-code -- JarvisMac.xcodeproj" in job
    assert "-scheme JarvisMac" in job
    assert "-configuration Release" in job
    assert "CODE_SIGNING_ALLOWED=NO" in job
    assert "Jarvis.app/Contents/PlugIns/JarvisWidget.appex" in job
