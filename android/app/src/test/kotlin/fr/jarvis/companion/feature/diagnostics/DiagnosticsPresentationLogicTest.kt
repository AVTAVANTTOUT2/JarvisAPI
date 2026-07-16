package fr.jarvis.companion.feature.diagnostics

import fr.jarvis.companion.core.connectivity.ConnectivityState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticsPresentationLogicTest {
    @Test
    fun computeGlobalVerdict_returnsProblemWhenOneSectionIsProblem() {
        val verdict = computeGlobalDiagnosticsVerdict(
            listOf(
                DiagnosticsSectionStatus("Application", DiagnosticsLevel.Ok),
                DiagnosticsSectionStatus("Connexion", DiagnosticsLevel.Problem),
            ),
        )

        assertEquals(DiagnosticsLevel.Problem, verdict.level)
    }

    @Test
    fun computeGlobalVerdict_returnsAttentionWhenNoProblem() {
        val verdict = computeGlobalDiagnosticsVerdict(
            listOf(
                DiagnosticsSectionStatus("Application", DiagnosticsLevel.Ok),
                DiagnosticsSectionStatus("GPS", DiagnosticsLevel.Attention),
            ),
        )

        assertEquals(DiagnosticsLevel.Attention, verdict.level)
    }

    @Test
    fun computeGlobalVerdict_returnsOkWhenAllSectionsAreOk() {
        val verdict = computeGlobalDiagnosticsVerdict(
            listOf(
                DiagnosticsSectionStatus("Application", DiagnosticsLevel.Ok),
                DiagnosticsSectionStatus("Connexion", DiagnosticsLevel.Ok),
            ),
        )

        assertEquals(DiagnosticsLevel.Ok, verdict.level)
        assertTrue(verdict.title.contains("opérationnel"))
    }

    @Test
    fun sanitizeDiagnosticValue_masksBearerTokensAndCoordinates() {
        val raw = "Bearer abcdef1234567890abcdef 48.8566,2.3522"
        val sanitized = sanitizeDiagnosticValue(raw)

        assertFalse(sanitized.contains("abcdef1234567890abcdef"))
        assertFalse(sanitized.contains("48.8566"))
        assertTrue(sanitized.contains("[secret masqué]"))
        assertTrue(sanitized.contains("[coordonnée masquée]"))
    }

    @Test
    fun maskServerHost_keepsHostAndPortOnly() {
        val masked = maskServerHost("https://jarvis.local:8081/api/status?token=abc")

        assertEquals("jarvis.local:8081", masked)
    }

    @Test
    fun maskServerHost_returnsInvalidForMalformedUrl() {
        assertEquals("(invalide)", maskServerHost(":::/bad-url"))
    }

    @Test
    fun maskDeviceId_keepsOnlySuffix() {
        assertEquals("android-***cdef", maskDeviceId("android-0123456789abcdef"))
    }

    @Test
    fun evaluateConnectionStatus_handlesOfflineAndUnauthorized() {
        val offline = evaluateConnectionStatus(
            connectivity = ConnectivityState.Offline,
            tokenPresent = true,
            onboardingComplete = true,
            serverConfigured = true,
        )
        val unauthorized = evaluateConnectionStatus(
            connectivity = ConnectivityState.Unauthorized,
            tokenPresent = true,
            onboardingComplete = true,
            serverConfigured = true,
        )

        assertEquals(DiagnosticsLevel.Attention, offline.level)
        assertTrue(offline.badgeLabel.contains("Hors ligne"))
        assertEquals(DiagnosticsLevel.Problem, unauthorized.level)
        assertTrue(unauthorized.badgeLabel.contains("révoqué"))
    }
}
