package fr.jarvis.companion.feature.location

import fr.jarvis.companion.core.connectivity.ConnectivityState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocationPresentationLogicTest {
    @Test
    fun deriveLocationHeroVerdict_returnsHealthyWhenSignalsAreGreen() {
        val verdict = deriveLocationHeroVerdict(
            LocationUiState(
                collectionEnabled = true,
                finePermission = true,
                backgroundPermission = true,
                pendingCount = 2,
                failedCount = 0,
                connectivity = ConnectivityState.ServerReachable,
                lastCaptureTime = "12:34",
            ),
        )

        assertEquals(LocationHealthLevel.Healthy, verdict.level)
        assertEquals("La localisation fonctionne", verdict.title)
    }

    @Test
    fun deriveLocationHeroVerdict_returnsProblemWhenTokenIsRevoked() {
        val verdict = deriveLocationHeroVerdict(
            LocationUiState(
                collectionEnabled = true,
                finePermission = true,
                backgroundPermission = true,
                connectivity = ConnectivityState.Unauthorized,
            ),
        )

        assertEquals(LocationHealthLevel.Problem, verdict.level)
        assertTrue(verdict.detail.contains("Jeton révoqué"))
    }

    @Test
    fun deriveLocationHeroVerdict_returnsAttentionWhenOffline() {
        val verdict = deriveLocationHeroVerdict(
            LocationUiState(
                collectionEnabled = true,
                finePermission = true,
                backgroundPermission = true,
                connectivity = ConnectivityState.Offline,
            ),
        )

        assertEquals(LocationHealthLevel.Attention, verdict.level)
        assertTrue(verdict.detail.contains("Hors ligne"))
    }

    @Test
    fun deriveLocationHeroVerdict_returnsProblemWhenPermissionMissing() {
        val verdict = deriveLocationHeroVerdict(
            LocationUiState(
                collectionEnabled = true,
                finePermission = false,
                backgroundPermission = false,
                connectivity = ConnectivityState.ServerReachable,
            ),
        )

        assertEquals(LocationHealthLevel.Problem, verdict.level)
        assertTrue(verdict.detail.contains("Permission"))
    }

    @Test
    fun sanitizeTimelineLabel_masksPreciseCoordinates() {
        val sanitized = sanitizeTimelineLabel("Point reçu 48.8566, 2.3522")

        assertEquals("Point reçu [coordonnée masquée], [coordonnée masquée]", sanitized)
    }

    @Test
    fun sanitizeTimelineLabel_keepsSimpleLabelUntouched() {
        assertEquals("Batch créé", sanitizeTimelineLabel("Batch créé"))
    }

    @Test
    fun formatLastCaptureSummary_handlesMissingValues() {
        assertEquals("Aucune capture récente", formatLastCaptureSummary(null, null))
        assertEquals("12:00", formatLastCaptureSummary("12:00", null))
    }
}
