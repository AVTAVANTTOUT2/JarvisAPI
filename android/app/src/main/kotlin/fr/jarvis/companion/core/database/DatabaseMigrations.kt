package fr.jarvis.companion.core.database

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
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
