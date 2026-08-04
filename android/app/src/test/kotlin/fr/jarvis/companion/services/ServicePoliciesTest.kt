package fr.jarvis.companion.services

import fr.jarvis.companion.notifications.JarvisNotifications
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServicePoliciesTest {
    @Test
    fun stopAlwaysWinsEvenWhenCredentialsAreMissing() {
        assertEquals(
            LocationServiceDecision.STOP,
            locationServiceDecision(
                action = JarvisLocationService.ACTION_STOP,
                hasNativeToken = false,
                hasLocationPermission = false,
            ),
        )
    }

    @Test
    fun cachedSyncRequiresTokenButNotCurrentLocationPermission() {
        assertEquals(
            LocationServiceDecision.REJECT_MISSING_PREREQUISITE,
            locationServiceDecision(
                action = JarvisLocationService.ACTION_SYNC,
                hasNativeToken = false,
                hasLocationPermission = true,
            ),
        )
        assertEquals(
            LocationServiceDecision.SYNC_CACHED,
            locationServiceDecision(
                action = JarvisLocationService.ACTION_SYNC,
                hasNativeToken = true,
                hasLocationPermission = false,
            ),
        )
    }

    @Test
    fun liveCaptureRequiresTokenAndLocationPermission() {
        assertEquals(
            LocationServiceDecision.REJECT_MISSING_PREREQUISITE,
            locationServiceDecision(null, hasNativeToken = true, hasLocationPermission = false),
        )
        assertEquals(
            LocationServiceDecision.START_CAPTURE,
            locationServiceDecision(null, hasNativeToken = true, hasLocationPermission = true),
        )
    }

    @Test
    fun wakeWordRejectsBlankKeysAndRequiresAudioPermission() {
        assertFalse(canStartWakeWord("   ", hasRecordAudioPermission = true))
        assertFalse(canStartWakeWord("picovoice-key", hasRecordAudioPermission = false))
        assertTrue(canStartWakeWord("picovoice-key", hasRecordAudioPermission = true))
    }

    @Test
    fun pushPriorityRoutingIsNormalized() {
        assertEquals(JarvisNotifications.URGENT, notificationChannelForPriority(" HIGH "))
        assertEquals(JarvisNotifications.URGENT, notificationChannelForPriority("urgent"))
        assertEquals(JarvisNotifications.DEFAULT, notificationChannelForPriority("medium"))
        assertEquals(JarvisNotifications.DEFAULT, notificationChannelForPriority("inconnue"))
    }
}
