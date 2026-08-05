"""Contrats empêchant le retour des API Android déjà retirées ou dépréciées."""

from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "app"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def _read(relative: str) -> str:
    return (ANDROID / relative).read_text(encoding="utf-8")


def test_fcm_uses_firebase_installation_registration() -> None:
    manifest = _read("src/main/AndroidManifest.xml")
    service = _read(
        "src/main/kotlin/fr/jarvis/companion/services/JarvisMessagingService.kt",
    )
    view_model = _read(
        "src/main/kotlin/fr/jarvis/companion/ui/MainViewModel.kt",
    )

    root = ElementTree.fromstring(manifest)
    metadata = {
        item.attrib[f"{ANDROID_NS}name"]: item.attrib[f"{ANDROID_NS}value"]
        for item in root.findall("application/meta-data")
    }
    assert metadata["firebase_messaging_installation_id_enabled"] == "true"
    assert "override fun onRegistered(installationId: String)" in service
    assert "onNewToken" not in service
    assert "FirebaseMessaging.getInstance().register()" in view_model
    assert "FirebaseMessaging.getInstance().token" not in view_model


def test_compose_lifecycle_owner_uses_lifecycle_runtime_compose() -> None:
    chat_screen = _read(
        "src/main/kotlin/fr/jarvis/companion/feature/chat/ChatScreen.kt",
    )

    assert "import androidx.lifecycle.compose.LocalLifecycleOwner" in chat_screen
    assert "import androidx.compose.ui.platform.LocalLifecycleOwner" not in chat_screen


def test_notification_builder_matches_minimum_supported_sdk() -> None:
    build = _read("build.gradle")
    notifications = _read(
        "src/main/kotlin/fr/jarvis/companion/notifications/JarvisNotifications.kt",
    )

    assert "minSdk 28" in build
    assert "Notification.Builder(context, channel)" in notifications
    assert "Notification.Builder(context)" not in notifications
    assert "allWarningsAsErrors = true" in build
