package fr.jarvis.companion

import fr.jarvis.companion.ui.MainActivity
import fr.jarvis.companion.voice.VoiceActivity
import org.junit.Assert.assertFalse
import org.junit.Test

/** Empêche le retour d'un WebView comme interface principale. */
class NoWebViewGuardTest {
    @Test
    fun mainActivityMustNotExtendWebViewWrapper() {
        assertFalse(
            "MainActivity ne doit pas étendre WebView",
            android.webkit.WebView::class.java.isAssignableFrom(MainActivity::class.java),
        )
    }

    @Test
    fun voiceActivityMustNotExtendWebViewWrapper() {
        assertFalse(
            "VoiceActivity ne doit pas étendre WebView",
            android.webkit.WebView::class.java.isAssignableFrom(VoiceActivity::class.java),
        )
    }
}
