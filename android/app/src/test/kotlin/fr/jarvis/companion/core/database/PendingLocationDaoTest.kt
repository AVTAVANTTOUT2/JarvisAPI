package fr.jarvis.companion.core.database

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PendingLocationDaoTest {
    private lateinit var context: Context
    private lateinit var database: JarvisDatabase

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        database = Room.inMemoryDatabaseBuilder(context, JarvisDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        runBlocking { database.locationSyncLockDao().ensureRow() }
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun insertAndCountByState() = runBlocking {
        val dao = database.pendingLocationDao()
        dao.insert(sampleEntity(syncState = PendingLocationSyncState.PENDING))
        assertEquals(1, dao.countByState(PendingLocationSyncState.PENDING))
    }

    @Test
    fun getEligibleForSync_respectsNextRetryAt() = runBlocking {
        val dao = database.pendingLocationDao()
        val now = System.currentTimeMillis()
        dao.insert(
            sampleEntity(
                clientPointId = "a",
                syncState = PendingLocationSyncState.FAILED_RETRYABLE,
                nextRetryAt = now + 60_000L,
            ),
        )
        dao.insert(
            sampleEntity(
                clientPointId = "b",
                syncState = PendingLocationSyncState.PENDING,
            ),
        )
        val eligible = dao.getEligibleForSync(limit = 10, now = now)
        assertEquals(1, eligible.size)
        assertEquals("b", eligible.first().clientPointId)
    }

    @Test
    fun reserveBatch_setsSendingState() = runBlocking {
        val dao = database.pendingLocationDao()
        val id = dao.insert(sampleEntity(clientPointId = "pt-1"))
        val now = System.currentTimeMillis()
        dao.reserveBatch(listOf(id), "batch-1", PendingLocationSyncState.SENDING, now)
        val list = dao.observeByState(PendingLocationSyncState.SENDING).first()
        assertEquals(1, list.size)
        assertEquals("batch-1", list.first().batchId)
    }

    @Test
    fun reclaimAllSending_returnsOrphansToPending() = runBlocking {
        val dao = database.pendingLocationDao()
        val now = System.currentTimeMillis()
        val id = dao.insert(sampleEntity(clientPointId = "orphan"))
        dao.reserveBatch(listOf(id), "dead-batch", PendingLocationSyncState.SENDING, now)
        assertEquals(1, dao.countByState(PendingLocationSyncState.SENDING))
        dao.reclaimAllSending()
        assertEquals(0, dao.countByState(PendingLocationSyncState.SENDING))
        assertEquals(1, dao.countByState(PendingLocationSyncState.PENDING))
    }

    @Test
    fun syncLock_acquireAndRelease() = runBlocking {
        val lockDao = database.locationSyncLockDao()
        val now = System.currentTimeMillis()
        val acquired = lockDao.tryAcquire("worker-1", now, now + 60_000L, now)
        assertEquals(1, acquired)
        val blocked = lockDao.tryAcquire("worker-2", now, now + 60_000L, now)
        assertEquals(0, blocked)
        lockDao.release("worker-1")
        val reacquired = lockDao.tryAcquire("worker-2", now, now + 60_000L, now)
        assertEquals(1, reacquired)
    }

    @Test
    fun freshDatabaseAcceptsPendingLocations() = runBlocking {
        val fresh = Room.inMemoryDatabaseBuilder(context, JarvisDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        val entity = fresh.pendingLocationDao().insert(sampleEntity(clientPointId = "migrated"))
        assertNotNull(entity)
        assertTrue(entity > 0)
        fresh.close()
    }

    private fun sampleEntity(
        clientPointId: String = "uuid-test",
        syncState: String = PendingLocationSyncState.PENDING,
        nextRetryAt: Long? = null,
    ): PendingLocationEntity = PendingLocationEntity(
        clientPointId = clientPointId,
        latitude = 50.63,
        longitude = 3.06,
        altitude = 20.0,
        accuracy = 12f,
        speed = 1f,
        bearing = 180f,
        provider = "gps",
        capturedAt = System.currentTimeMillis(),
        createdAt = System.currentTimeMillis(),
        syncState = syncState,
        nextRetryAt = nextRetryAt,
    )
}
