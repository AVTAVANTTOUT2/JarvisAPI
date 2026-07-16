package fr.jarvis.companion.core.database

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        CachedBriefingEntity::class,
        CachedTaskEntity::class,
        CachedEventEntity::class,
        CachedNotificationEntity::class,
        SyncMetadataEntity::class,
        PendingLocationEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class JarvisDatabase : RoomDatabase() {
    abstract fun cachedBriefingDao(): CachedBriefingDao
    abstract fun cachedTaskDao(): CachedTaskDao
    abstract fun cachedEventDao(): CachedEventDao
    abstract fun cachedNotificationDao(): CachedNotificationDao
    abstract fun syncMetadataDao(): SyncMetadataDao
    abstract fun pendingLocationDao(): PendingLocationDao

    companion object {
        private const val DB_NAME = "jarvis_companion.db"

        @Volatile
        private var instance: JarvisDatabase? = null

        fun getInstance(context: Context): JarvisDatabase {
            return instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    JarvisDatabase::class.java,
                    DB_NAME,
                ).build().also { instance = it }
            }
        }
    }
}
