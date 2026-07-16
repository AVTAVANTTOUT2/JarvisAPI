package fr.jarvis.companion.core.sync

import androidx.work.ExistingWorkPolicy
import fr.jarvis.companion.core.location.LocationConstants
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocationSyncWorkerTest {
    @Test
    fun immediateSyncReplacesQueuedOneShotSoLivePointsAreNotBlocked() {
        assertEquals(ExistingWorkPolicy.REPLACE, LocationSyncWorker.IMMEDIATE_WORK_POLICY)
    }

    @Test
    fun lockAndSendingReclaimAreShortEnoughForForceStopRecovery() {
        assertTrue(
            "LOCK_TTL must recover within ~2 minutes after force-stop",
            LocationConstants.LOCK_TTL_MS <= 120_000L,
        )
        assertTrue(
            "SENDING reclaim safety net must be aligned with lock TTL",
            LocationConstants.SENDING_RECLAIM_MS <= LocationConstants.LOCK_TTL_MS,
        )
    }
}
