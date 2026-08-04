package fr.jarvis.companion.receivers

import android.content.Intent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BootRecoveryPolicyTest {
    @Test
    fun receiverAcceptsOnlyManifestBootActions() {
        assertTrue(isSupportedBootAction(Intent.ACTION_BOOT_COMPLETED))
        assertTrue(isSupportedBootAction(Intent.ACTION_MY_PACKAGE_REPLACED))
        assertFalse(isSupportedBootAction(null))
        assertFalse(isSupportedBootAction("fr.jarvis.action.FORGE"))
    }

    @Test
    fun missingTokenPreventsSyncAndLocationServiceRestart() {
        val plan = bootRecoveryPlan(
            hasNativeToken = false,
            locationEnabled = true,
            hasLocationPermission = true,
            wakeWordEnabled = false,
        )

        assertEquals(
            BootRecoveryPlan(
                scheduleLocationSync = false,
                startLocationService = false,
                showWakeWordReminder = false,
            ),
            plan,
        )
    }

    @Test
    fun pairedDeviceRestoresOnlyServicesWhosePrerequisitesAreMet() {
        val withoutPermission = bootRecoveryPlan(
            hasNativeToken = true,
            locationEnabled = true,
            hasLocationPermission = false,
            wakeWordEnabled = true,
        )
        assertTrue(withoutPermission.scheduleLocationSync)
        assertFalse(withoutPermission.startLocationService)
        assertTrue(withoutPermission.showWakeWordReminder)

        val ready = bootRecoveryPlan(
            hasNativeToken = true,
            locationEnabled = true,
            hasLocationPermission = true,
            wakeWordEnabled = false,
        )
        assertTrue(ready.scheduleLocationSync)
        assertTrue(ready.startLocationService)
        assertFalse(ready.showWakeWordReminder)
    }
}
