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
        ChatConversationEntity::class,
        ChatMessageEntity::class,
        PendingChatOperationEntity::class,
        ChatDraftEntity::class,
    ],
    version = 2,
    exportSchema = false,
)
abstract class JarvisDatabase : RoomDatabase() {
    abstract fun cachedBriefingDao(): CachedBriefingDao
    abstract fun cachedTaskDao(): CachedTaskDao
    abstract fun cachedEventDao(): CachedEventDao
    abstract fun cachedNotificationDao(): CachedNotificationDao
    abstract fun syncMetadataDao(): SyncMetadataDao
    abstract fun pendingLocationDao(): PendingLocationDao
    abstract fun chatConversationDao(): ChatConversationDao
    abstract fun chatMessageDao(): ChatMessageDao
    abstract fun pendingChatOperationDao(): PendingChatOperationDao
    abstract fun chatDraftDao(): ChatDraftDao

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
                )
                    .addMigrations(MIGRATION_1_2)
                    .build()
                    .also { instance = it }
            }
        }
    }
}
