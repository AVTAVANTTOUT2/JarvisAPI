package fr.jarvis.companion.core.database

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RoomCacheTest {
    private lateinit var context: Context
    private lateinit var database: JarvisDatabase

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        database = Room.inMemoryDatabaseBuilder(context, JarvisDatabase::class.java)
            .allowMainThreadQueries()
            .build()
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun insertBriefingAndObserve() = runBlocking {
        val dao = database.cachedBriefingDao()
        val entity = CachedBriefingEntity(
            kind = "morning",
            content = "Bonjour Monsieur. Trois tâches en attente.",
            fetchedAtMillis = System.currentTimeMillis(),
            validForDate = "2026-07-16",
        )
        dao.upsert(entity)

        val observed = dao.observeLatest().first()
        assertNotNull(observed)
        assertEquals("morning", observed?.kind)
        assertEquals("Bonjour Monsieur. Trois tâches en attente.", observed?.content)
    }
}
