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
    }
}
