package fr.jarvis.companion.data.chat

import com.google.gson.Gson
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.database.ChatConversationDao
import fr.jarvis.companion.core.database.ConversationSyncState
import fr.jarvis.companion.core.database.PendingChatOpState
import fr.jarvis.companion.core.database.PendingChatOpType
import fr.jarvis.companion.core.database.PendingChatOperationDao
import fr.jarvis.companion.core.database.PendingChatOperationEntity
import fr.jarvis.companion.data.JarvisRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.format.DateTimeFormatter

class ConversationRepository(
    private val conversationDao: ChatConversationDao,
    private val pendingOpDao: PendingChatOperationDao,
    private val repository: JarvisRepository,
    private val gson: Gson = Gson(),
) {
    fun observeConversations(): Flow<List<ChatConversationEntity>> = conversationDao.observeActive()

    fun searchConversations(query: String): Flow<List<ChatConversationEntity>> =
        conversationDao.search(query.trim())

    fun observeConversation(localId: Long): Flow<ChatConversationEntity?> =
        conversationDao.observeByLocalId(localId)

    suspend fun refreshFromServer(): RefreshResult = withContext(Dispatchers.IO) {
        val result = repository.getConversations(limit = 100)
        if (result.status == 401) return@withContext RefreshResult(unauthorized = true)
        if (!result.ok) return@withContext RefreshResult(error = result.error)

        val array = result.json.optJSONArray("conversations") ?: JSONArray()
        val now = System.currentTimeMillis()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            upsertFromServerJson(item, now)
        }
        RefreshResult(ok = true)
    }

    suspend fun createConversation(title: String? = null): Long = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val localId = conversationDao.insert(
            ChatConversationEntity(
                title = title?.trim().orEmpty().ifBlank { "Nouvelle conversation" },
                createdAtMillis = now,
                updatedAtMillis = now,
                syncState = ConversationSyncState.PENDING_CREATE,
            ),
        )
        val payload = gson.toJson(mapOf("title" to title))
        pendingOpDao.insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.CREATE_CONVERSATION,
                conversationLocalId = localId,
                payloadJson = payload,
                createdAtMillis = now,
            ),
        )
        localId
    }

    suspend fun renameConversation(localId: Long, newTitle: String) = withContext(Dispatchers.IO) {
        val conv = conversationDao.getByLocalId(localId) ?: return@withContext
        val now = System.currentTimeMillis()
        conversationDao.update(
            conv.copy(
                title = newTitle.trim(),
                updatedAtMillis = now,
                syncState = if (conv.serverId != null) ConversationSyncState.PENDING_UPDATE else conv.syncState,
            ),
        )
        if (conv.serverId != null) {
            enqueueOp(localId, conv.serverId, PendingChatOpType.RENAME, mapOf("title" to newTitle.trim()))
        }
    }

    suspend fun togglePin(localId: Long) = withContext(Dispatchers.IO) {
        val conv = conversationDao.getByLocalId(localId) ?: return@withContext
        val now = System.currentTimeMillis()
        val newPinned = !conv.isPinned
        conversationDao.update(
            conv.copy(
                isPinned = newPinned,
                updatedAtMillis = now,
                syncState = if (conv.serverId != null) ConversationSyncState.PENDING_UPDATE else conv.syncState,
            ),
        )
        if (conv.serverId != null) {
            val type = if (newPinned) PendingChatOpType.PIN else PendingChatOpType.UNPIN
            enqueueOp(localId, conv.serverId, type, emptyMap())
        }
    }

    suspend fun archiveConversation(localId: Long) = withContext(Dispatchers.IO) {
        val conv = conversationDao.getByLocalId(localId) ?: return@withContext
        val now = System.currentTimeMillis()
        conversationDao.update(
            conv.copy(
                isArchived = true,
                updatedAtMillis = now,
                syncState = if (conv.serverId != null) ConversationSyncState.PENDING_UPDATE else conv.syncState,
            ),
        )
        if (conv.serverId != null) {
            enqueueOp(localId, conv.serverId, PendingChatOpType.ARCHIVE, emptyMap())
        }
    }

    suspend fun deleteConversation(localId: Long) = withContext(Dispatchers.IO) {
        val conv = conversationDao.getByLocalId(localId) ?: return@withContext
        if (conv.serverId == null) {
            conversationDao.deleteByLocalId(localId)
            pendingOpDao.deleteForConversation(localId)
            return@withContext
        }
        conversationDao.markPendingDeletion(localId, ConversationSyncState.PENDING_DELETE)
        enqueueOp(localId, conv.serverId, PendingChatOpType.DELETE, emptyMap())
    }

    suspend fun applyServerConversationCreated(localId: Long, serverId: Long, title: String?) =
        withContext(Dispatchers.IO) {
            val conv = conversationDao.getByLocalId(localId) ?: return@withContext
            val now = System.currentTimeMillis()
            conversationDao.update(
                conv.copy(
                    serverId = serverId,
                    title = title?.takeIf { it.isNotBlank() } ?: conv.title,
                    syncState = ConversationSyncState.SYNCED,
                    lastError = null,
                    updatedAtMillis = now,
                ),
            )
        }

    private suspend fun enqueueOp(
        localId: Long,
        serverId: Long?,
        type: String,
        payload: Map<String, Any?>,
    ) {
        pendingOpDao.insert(
            PendingChatOperationEntity(
                type = type,
                conversationLocalId = localId,
                conversationServerId = serverId,
                payloadJson = gson.toJson(payload),
                createdAtMillis = System.currentTimeMillis(),
            ),
        )
    }

    private suspend fun upsertFromServerJson(item: JSONObject, now: Long) {
        val serverId = item.optLong("id", -1)
        if (serverId < 0) return
        val existing = conversationDao.getByServerId(serverId)
        if (existing?.pendingDeletion == true) return

        val title = item.optString("title").ifBlank { "Conversation #$serverId" }
        val pinned = item.optBoolean("pinned", false)
        val archived = item.optBoolean("archived", false)
        val lastMessage = item.optString("last_message").takeIf { it.isNotBlank() }
        val lastAt = parseIsoMillis(item.optString("last_message_at"))
            ?: parseIsoMillis(item.optString("started_at"))
            ?: now

        if (existing == null) {
            conversationDao.insert(
                ChatConversationEntity(
                    serverId = serverId,
                    title = title,
                    isPinned = pinned,
                    isArchived = archived,
                    createdAtMillis = lastAt,
                    updatedAtMillis = now,
                    lastMessageAtMillis = lastAt,
                    lastMessagePreview = lastMessage,
                    syncState = ConversationSyncState.SYNCED,
                ),
            )
        } else if (existing.syncState == ConversationSyncState.SYNCED ||
            existing.syncState == ConversationSyncState.ERROR
        ) {
            conversationDao.update(
                existing.copy(
                    title = title,
                    isPinned = pinned,
                    isArchived = archived,
                    lastMessageAtMillis = lastAt,
                    lastMessagePreview = lastMessage,
                    updatedAtMillis = now,
                    syncState = ConversationSyncState.SYNCED,
                    lastError = null,
                ),
            )
        }
    }

    private fun parseIsoMillis(iso: String?): Long? {
        if (iso.isNullOrBlank()) return null
        return runCatching {
            Instant.from(DateTimeFormatter.ISO_OFFSET_DATE_TIME.parse(iso)).toEpochMilli()
        }.getOrElse {
            runCatching {
                Instant.parse(iso).toEpochMilli()
            }.getOrNull()
        }
    }

    data class RefreshResult(
        val ok: Boolean = false,
        val unauthorized: Boolean = false,
        val error: String? = null,
    )
}
