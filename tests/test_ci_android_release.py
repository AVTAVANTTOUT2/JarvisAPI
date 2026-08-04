"""Contrats structurels de la vraie porte de release Android/R8."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PROGUARD = ROOT / "android" / "app" / "proguard-rules.pro"


def _android_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow[workflow.index("  android:") :]


def test_ci_builds_and_tests_the_release_variant_with_r8():
    job = _android_job()

    for task in (
        "assembleRelease",
        "testReleaseUnitTest",
        "lintRelease",
        "connectedDebugAndroidTest",
    ):
        assert task in job
    assert "app-release-unsigned.apk" in job
    assert "outputs/mapping/release/mapping.txt" in job


def test_ci_checks_major_dtos_survive_r8_with_stable_names():
    job = _android_job()
    rules = PROGUARD.read_text(encoding="utf-8")

    for dto in (
        "fr.jarvis.companion.network.LocationBatchResponse",
        "fr.jarvis.companion.network.LocationDiagnosticsResponse",
        "fr.jarvis.companion.voice.VoiceTurnResponse",
    ):
        assert dto in job

    assert "-keep interface fr.jarvis.companion.network.JarvisApiService" in rules
    assert "fr.jarvis.companion.network.**Request" in rules
    assert "fr.jarvis.companion.network.**Response" in rules
    assert "fr.jarvis.companion.voice.VoiceTurnResponse" in rules
    assert "RuntimeVisibleAnnotations" in rules


def test_ci_runs_android_keystore_and_ui_tests_on_an_emulator():
    job = _android_job()

    assert "99-kvm4all.rules" in job
    assert "udevadm trigger --name-match=kvm" in job
    assert "reactivecircus/android-emulator-runner@v2" in job
    assert "api-level: 35" in job
    assert "working-directory: android" in job
    assert "JarvisSecureStoreInstrumentedTest" in (
        ROOT / "android" / "app" / "src" / "androidTest" / "kotlin" /
        "fr" / "jarvis" / "companion" / "data" /
        "JarvisSecureStoreInstrumentedTest.kt"
    ).read_text(encoding="utf-8")
