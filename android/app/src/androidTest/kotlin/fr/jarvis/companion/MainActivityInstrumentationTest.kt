package fr.jarvis.companion

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MainActivityInstrumentationTest {
    @Test
    fun mainActivityIsNativeCompose_notWebViewWrapper() {
        val activityClass = Class.forName("fr.jarvis.companion.ui.MainActivity")
        assertFalse(
            "WebView ne doit pas être champ de MainActivity",
            activityClass.declaredFields.any { it.type.name == "android.webkit.WebView" },
        )
        assertFalse(
            "MainActivity ne doit pas importer WebView",
            activityClass.protectionDomain?.codeSource?.location?.toString()?.contains("WebView") == true,
        )
    }
}
