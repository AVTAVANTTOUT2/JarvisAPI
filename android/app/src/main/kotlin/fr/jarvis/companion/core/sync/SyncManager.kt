package fr.jarvis.companion.core.sync

import android.content.Context
import fr.jarvis.companion.core.connectivity.ConnectivityObserver
import fr.jarvis.companion.core.database.CachedBriefingEntity
import fr.jarvis.companion.core.database.CachedEventEntity
import fr.jarvis.companion.core.database.CachedNotificationEntity
import fr.jarvis.companion.core.database.CachedTaskEntity
import fr.jarvis.companion.core.database.JarvisDatabase
import fr.jarvis.companion.core.database.SyncMetadataEntity
import fr.jarvis.companion.data.JarvisRepository
import fr.jarvis.companion.data.JarvisSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

data class SyncResult(
    val ok: Boolean,
    val unauthorized: Boolean,
    val message: String,
    val partialErrors: List<String>,
)

class SyncManager(
    private val context: Context,
    private val database: JarvisDatabase,
    private val repository: JarvisRepository,
    private val connectivityObserver: ConnectivityObserver,
) {
    private val appContext = context.applicationContext

    suspend fun refreshHome(): SyncResult = withContext(Dispatchers.IO) {
        if (!JarvisSettings.hasServerConfigured(appContext) ||
            JarvisSettings.nativeToken(appContext).isEmpty()
        ) {
            return@withContext SyncResult(
                ok = false,
                unauthorized = false,
                message = "Appairage ou serveur requis",
                partialErrors = emptyList(),
            )
        }

        connectivityObserver.resetServerState()
        val errors = mutableListOf<String>()
        var sawUnauthorized = false
        var anySuccess = false

        val briefingKind = resolveBriefingKind()
        val briefing = repository.getBriefing(briefingKind)
        when {
            briefing.status == 401 -> sawUnauthorized = true
            briefing.ok -> {
                cacheBriefing(briefingKind, briefing.json)
                upsertMeta(SYNC_BRIEFING, null)
                anySuccess = true
            }
            else -> errors.add("Briefing : ${briefing.error}")
        }

        val tasks = repository.getTasks()
        when {
            tasks.status == 401 -> sawUnauthorized = true
            tasks.ok -> {
                cacheTasks(tasks.json.optJSONArray("tasks"))
                upsertMeta(SYNC_TASKS, null)
                anySuccess = true
            }
            else -> errors.add("Tâches : ${tasks.error}")
        }

        val (startIso, endIso) = calendarWindow()
        val calendar = repository.getCalendar(startIso, endIso)
        when {
            calendar.status == 401 -> sawUnauthorized = true
            calendar.ok -> {
                cacheEvents(calendar.json.optJSONArray("events"))
                upsertMeta(SYNC_CALENDAR, null)
                anySuccess = true
            }
            else -> errors.add("Agenda : ${calendar.error}")
        }

        val notifications = repository.getNotifications()
        when {
            notifications.status == 401 -> sawUnauthorized = true
            notifications.ok -> {
                cacheNotifications(notifications.json.optJSONArray("notifications"))
                upsertMeta(SYNC_NOTIFICATIONS, null)
                anySuccess = true
            }
            else -> errors.add("Notifications : ${notifications.error}")
        }

        val conversations = repository.getConversations()
        when {
            conversations.status == 401 -> sawUnauthorized = true
            conversations.ok -> {
                upsertMeta(SYNC_CONVERSATIONS, null)
                anySuccess = true
            }
            else -> errors.add("Conversations : ${conversations.error}")
        }

        if (sawUnauthorized) {
            connectivityObserver.reportUnauthorized()
            upsertMeta(SYNC_HOME, "Session expirée ou révoquée")
            return@withContext SyncResult(
                ok = false,
                unauthorized = true,
                message = "Session expirée — réappairez le téléphone",
                partialErrors = errors,
            )
        }

        if (anySuccess) {
            connectivityObserver.reportServerReachable()
            upsertMeta(SYNC_HOME, null)
        } else {
            connectivityObserver.reportServerUnreachable()
            upsertMeta(SYNC_HOME, errors.joinToString(" ; "))
        }

        SyncResult(
            ok = anySuccess && errors.isEmpty(),
            unauthorized = false,
            message = when {
                errors.isEmpty() -> "Synchronisation réussie"
                anySuccess -> "Synchronisation partielle"
                else -> "Synchronisation impossible"
            },
            partialErrors = errors,
        )
    }

    private suspend fun cacheBriefing(kind: String, json: org.json.JSONObject) {
        val content = json.optString("content", "")
        if (content.isBlank()) return
        val today = LocalDate.now(ZoneId.systemDefault()).format(DateTimeFormatter.ISO_LOCAL_DATE)
        database.cachedBriefingDao().upsert(
            CachedBriefingEntity(
                kind = json.optString("kind", kind),
                content = content,
                fetchedAtMillis = System.currentTimeMillis(),
                validForDate = today,
            ),
        )
        database.cachedBriefingDao().deleteStaleForKind(kind, today)
    }

    private suspend fun cacheTasks(array: JSONArray?) {
        if (array == null) return
        val entities = mutableListOf<CachedTaskEntity>()
        val ids = mutableListOf<Long>()
        val now = System.currentTimeMillis()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            val id = item.optLong("id", -1)
            if (id < 0) continue
            ids.add(id)
            entities.add(
                CachedTaskEntity(
                    serverId = id,
                    title = item.optString("title", ""),
                    description = item.optString("description", ""),
                    priority = item.optString("priority", "medium"),
                    status = item.optString("status", "todo"),
                    dueDate = item.optString("due_date").takeIf { it.isNotBlank() },
                    category = item.optString("category").takeIf { it.isNotBlank() },
                    updatedAtMillis = now,
                ),
            )
        }
        if (entities.isNotEmpty()) {
            database.cachedTaskDao().upsertAll(entities)
            database.cachedTaskDao().deleteNotIn(ids)
        }
    }

    private suspend fun cacheNotifications(array: JSONArray?) {
        if (array == null) return
        val entities = mutableListOf<CachedNotificationEntity>()
        val ids = mutableListOf<Long>()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            val id = item.optLong("id", -1)
            if (id < 0) continue
            ids.add(id)
            entities.add(
                CachedNotificationEntity(
                    serverId = id,
                    source = item.optString("source", ""),
                    title = item.optString("title", ""),
                    content = item.optString("content", ""),
                    priority = item.optString("priority", "medium"),
                    read = item.optBoolean("read", false),
                    createdAt = item.optString("created_at", ""),
                ),
            )
        }
        if (entities.isNotEmpty()) {
            database.cachedNotificationDao().upsertAll(entities)
            database.cachedNotificationDao().deleteNotIn(ids)
        } else {
            database.cachedNotificationDao().deleteNotIn(listOf(-1L))
        }
    }

    private suspend fun cacheEvents(array: JSONArray?) {
        database.cachedEventDao().deleteAll()
        if (array == null || array.length() == 0) return
        val now = System.currentTimeMillis()
        val entities = (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            CachedEventEntity(
                serverId = item.optString("id", "evt-$index"),
                title = item.optString("title", item.optString("summary", "")),
                startIso = item.optString("start", ""),
                endIso = item.optString("end").takeIf { it.isNotBlank() },
                location = item.optString("location").takeIf { it.isNotBlank() },
                notes = item.optString("notes").takeIf { it.isNotBlank() },
                updatedAtMillis = now,
            )
        }
        if (entities.isNotEmpty()) {
            database.cachedEventDao().upsertAll(entities)
        }
    }

    private suspend fun upsertMeta(key: String, error: String?) {
        database.syncMetadataDao().upsert(
            SyncMetadataEntity(
                key = key,
                lastSuccessAtMillis = if (error == null) System.currentTimeMillis() else null,
                lastError = error,
            ),
        )
    }

    private fun resolveBriefingKind(): String {
        val hour = java.time.LocalTime.now(ZoneId.systemDefault()).hour
        return if (hour >= 17) "evening" else "morning"
    }

    private fun calendarWindow(): Pair<String, String> {
        val zone = ZoneId.systemDefault()
        val start = LocalDate.now(zone).atStartOfDay(zone).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
        val end = LocalDate.now(zone).plusDays(7).atStartOfDay(zone).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
        return start to end
    }

    companion object {
        const val SYNC_HOME = "home"
        const val SYNC_BRIEFING = "briefing"
        const val SYNC_TASKS = "tasks"
        const val SYNC_CALENDAR = "calendar"
        const val SYNC_NOTIFICATIONS = "notifications"
        const val SYNC_CONVERSATIONS = "conversations"
    }
}
