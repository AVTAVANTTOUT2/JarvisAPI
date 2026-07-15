package fr.jarvis.companion

import fr.jarvis.companion.ui.MainActivity
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
}
