package fr.jarvis.companion.feature.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsPresentationLogicTest {
    @Test
    fun evaluateServerSave_returnsValidationErrorWhenUrlIsInvalid() {
        val result = evaluateServerSave(
            rawInput = "http://insecure.local",
            currentServer = "https://jarvis.local",
            normalizer = { null },
        )

        assertEquals("Adresse invalide", result.errorMessage)
        assertNull(result.successMessage)
        assertFalse(result.shouldRevokeLocalToken)
        assertNull(result.normalizedServerUrl)
    }

    @Test
    fun evaluateServerSave_marksTokenRevocationWhenServerChanges() {
        val result = evaluateServerSave(
            rawInput = "https://new.jarvis.local",
            currentServer = "https://jarvis.local",
            normalizer = { "https://new.jarvis.local" },
        )

        assertNull(result.errorMessage)
        assertEquals("Serveur enregistré. Jeton local révoqué.", result.successMessage)
        assertTrue(result.shouldRevokeLocalToken)
        assertEquals("https://new.jarvis.local", result.normalizedServerUrl)
    }

    @Test
    fun evaluateServerSave_keepsTokenWhenServerIsUnchanged() {
        val result = evaluateServerSave(
            rawInput = "https://jarvis.local",
            currentServer = "https://jarvis.local",
            normalizer = { "https://jarvis.local" },
        )

        assertNull(result.errorMessage)
        assertEquals("Serveur déjà enregistré.", result.successMessage)
        assertFalse(result.shouldRevokeLocalToken)
        assertEquals("https://jarvis.local", result.normalizedServerUrl)
    }

    @Test
    fun sanitizePorcupineKey_returnsNullWhenBlank() {
        assertNull(sanitizePorcupineKey("   "))
    }

    @Test
    fun sanitizePorcupineKey_trimsValidValue() {
        assertEquals("pk_live_123", sanitizePorcupineKey(" pk_live_123 "))
    }
}
