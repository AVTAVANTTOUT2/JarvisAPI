package fr.jarvis.companion.core.database

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS pending_locations_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                clientPointId TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL,
                accuracy REAL NOT NULL,
                speed REAL,
                bearing REAL,
                provider TEXT,
                capturedAt INTEGER NOT NULL,
                createdAt INTEGER NOT NULL,
                syncState TEXT NOT NULL,
                batchId TEXT,
                retryCount INTEGER NOT NULL DEFAULT 0,
                nextRetryAt INTEGER,
                lastAttemptAt INTEGER,
                lastErrorCode TEXT,
                lastErrorMessage TEXT
            )
            """.trimIndent(),
        )

        val count = db.query("SELECT COUNT(*) FROM pending_locations").use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }

        if (count > 0) {
            db.execSQL(
                """
                INSERT INTO pending_locations_new (
                    id, clientPointId, latitude, longitude, altitude, accuracy, speed, bearing,
                    provider, capturedAt, createdAt, syncState, batchId, retryCount,
                    nextRetryAt, lastAttemptAt, lastErrorCode, lastErrorMessage
                )
                SELECT
                    id,
                    lower(hex(randomblob(16))),
                    latitude,
                    longitude,
                    altitude,
                    accuracy,
                    speed,
                    bearing,
                    provider,
                    capturedAtMillis,
                    createdAtMillis,
                    CASE syncState
                        WHEN 'pending' THEN 'PENDING'
                        WHEN 'synced' THEN 'SYNCED'
                        WHEN 'failed' THEN 'FAILED_RETRYABLE'
                        WHEN 'PENDING' THEN 'PENDING'
                        WHEN 'SYNCED' THEN 'SYNCED'
                        ELSE 'PENDING'
                    END,
                    NULL,
                    retryCount,
                    NULL,
                    NULL,
                    NULL,
                    lastError
                FROM pending_locations
                """.trimIndent(),
            )
        }

        db.execSQL("DROP TABLE pending_locations")
        db.execSQL("ALTER TABLE pending_locations_new RENAME TO pending_locations")

        db.execSQL(
            "CREATE UNIQUE INDEX IF NOT EXISTS index_pending_locations_clientPointId ON pending_locations (clientPointId)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_pending_locations_syncState ON pending_locations (syncState)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_pending_locations_capturedAt ON pending_locations (capturedAt)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_pending_locations_nextRetryAt ON pending_locations (nextRetryAt)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_pending_locations_batchId ON pending_locations (batchId)",
        )

        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS location_sync_lock (
                id INTEGER NOT NULL PRIMARY KEY,
                lockedBy TEXT,
                lockedAt INTEGER,
                expiresAt INTEGER
            )
            """.trimIndent(),
        )
        db.execSQL(
            "INSERT OR IGNORE INTO location_sync_lock (id, lockedBy, lockedAt, expiresAt) VALUES (1, NULL, NULL, NULL)",
        )

        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS chat_conversations (
                localId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                serverId INTEGER,
                title TEXT NOT NULL,
                isPinned INTEGER NOT NULL,
                isArchived INTEGER NOT NULL,
                createdAtMillis INTEGER NOT NULL,
                updatedAtMillis INTEGER NOT NULL,
                lastMessageAtMillis INTEGER,
                lastMessagePreview TEXT,
                syncState TEXT NOT NULL,
                lastError TEXT,
                pendingDeletion INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                localId INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                serverId INTEGER,
                conversationLocalId INTEGER NOT NULL,
                conversationServerId INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                createdAtMillis INTEGER NOT NULL,
                updatedAtMillis INTEGER NOT NULL,
                deliveryState TEXT NOT NULL,
                clientRequestId TEXT,
                errorMessage TEXT,
                isStreaming INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS pending_chat_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                type TEXT NOT NULL,
                conversationLocalId INTEGER NOT NULL,
                conversationServerId INTEGER,
                payloadJson TEXT NOT NULL,
                createdAtMillis INTEGER NOT NULL,
                retryCount INTEGER NOT NULL,
                nextAttemptAtMillis INTEGER NOT NULL,
                lastError TEXT,
                state TEXT NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS chat_drafts (
                conversationLocalId INTEGER PRIMARY KEY NOT NULL,
                draftText TEXT NOT NULL,
                updatedAtMillis INTEGER NOT NULL
            )
            """.trimIndent(),
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_chat_messages_conversationLocalId ON chat_messages(conversationLocalId)",
        )
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS index_chat_messages_deliveryState ON chat_messages(deliveryState)",
        )
        db.execSQL(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS index_chat_messages_clientRequestId
            ON chat_messages(clientRequestId) WHERE clientRequestId IS NOT NULL
            """.trimIndent(),
        )
    }
}
