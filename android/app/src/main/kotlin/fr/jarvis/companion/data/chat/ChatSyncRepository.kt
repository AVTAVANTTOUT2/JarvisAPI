package fr.jarvis.companion.data.chat

import com.google.gson.Gson
import fr.jarvis.companion.core.database.ChatConversationDao
import fr.jarvis.companion.core.database.ChatMessageDao
import fr.jarvis.companion.core.database.ConversationSyncState
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.database.PendingChatOpState
import fr.jarvis.companion.core.database.PendingChatOpType
import fr.jarvis.companion.core.database.PendingChatOperationDao
import fr.jarvis.companion.data.JarvisRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

class ChatSyncRepository(
    private val pendingOpDao: PendingChatOperationDao,
    private val conversationDao: ChatConversationDao,
    private val messageDao: ChatMessageDao,
    private val conversationRepository: ConversationRepository,
    private val chatRepository: ChatRepository,
    private val repository: JarvisRepository,
    private val gson: Gson = Gson(),
) {
    suspend fun processPendingOperations(): SyncChatResult = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val ops = pendingOpDao.getReady(now)
        if (ops.isEmpty()) return@withContext SyncChatResult(processed = 0)

        var processed = 0
        var unauthorized = false
        val errors = mutableListOf<String>()

        val byConversation = ops.groupBy { it.conversationLocalId }
        for ((_, convOps) in byConversation) {
            for (op in convOps.sortedBy { it.createdAtMillis }) {
                val result = processOne(op)
                when {
                    result.unauthorized -> {
                        unauthorized = true
                        break
                    }
                    result.ok -> processed++
                    result.error != null -> errors.add(result.error)
                }
            }
            if (unauthorized) break
        }
        SyncChatResult(processed = processed, unauthorized = unauthorized, errors = errors)
    }

    private suspend fun processOne(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val inFlight = op.copy(state = PendingChatOpState.IN_FLIGHT)
        pendingOpDao.update(inFlight)

        return when (op.type) {
            PendingChatOpType.CREATE_CONVERSATION -> processCreateConversation(inFlight)
            PendingChatOpType.SEND_MESSAGE -> processSendMessage(inFlight)
            PendingChatOpType.RENAME -> processRename(inFlight)
            PendingChatOpType.PIN, PendingChatOpType.UNPIN -> processPin(inFlight)
            PendingChatOpType.ARCHIVE -> processArchive(inFlight)
            PendingChatOpType.DELETE -> processDelete(inFlight)
            else -> OpResult(error = "Type inconnu : ${op.type}")
        }
    }

    private suspend fun processCreateConversation(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val payload = JSONObject(op.payloadJson)
        val title = payload.optString("title").takeIf { it.isNotBlank() }
        val result = repository.createMobileConversation(title)
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) return markRetry(op, result.error)

        val serverId = result.json.optLong("conversation_id", -1)
        if (serverId < 0) return markRetry(op, "Réponse sans conversation_id")
        conversationRepository.applyServerConversationCreated(op.conversationLocalId, serverId, title)
        pendingOpDao.delete(op.id)
        return OpResult(ok = true)
    }

    private suspend fun processSendMessage(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val payload = JSONObject(op.payloadJson)
        val content = payload.optString("content")
        val clientRequestId = payload.optString("clientRequestId")
        val userMessageLocalId = payload.optLong("userMessageLocalId", -1)
        val conv = conversationDao.getByLocalId(op.conversationLocalId)
        val serverId = conv?.serverId ?: op.conversationServerId

        val result = repository.sendMobileChat(
            content = content,
            conversationId = serverId,
            clientMessageId = clientRequestId.takeIf { it.isNotBlank() },
        )
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) {
            if (userMessageLocalId > 0) {
                messageDao.updateDeliveryState(
                    userMessageLocalId,
                    DeliveryState.FAILED_RETRYABLE,
                    result.error,
                    System.currentTimeMillis(),
                )
            }
            return markRetry(op, result.error)
        }

        if (userMessageLocalId > 0 && clientRequestId.isNotBlank()) {
            chatRepository.applyHttpChatResponse(
                conversationLocalId = op.conversationLocalId,
                userMessageLocalId = userMessageLocalId,
                clientRequestId = clientRequestId,
                json = result.json,
            )
        }
        pendingOpDao.delete(op.id)
        return OpResult(ok = true)
    }

    private suspend fun processRename(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val serverId = op.conversationServerId ?: return deleteInvalid(op)
        val payload = JSONObject(op.payloadJson)
        val title = payload.optString("title")
        val result = repository.patchConversation(serverId, title = title)
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) return markRetry(op, result.error)
        markConversationSynced(op.conversationLocalId)
        pendingOpDao.delete(op.id)
        return OpResult(ok = true)
    }

    private suspend fun processPin(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val serverId = op.conversationServerId ?: return deleteInvalid(op)
        val result = repository.pinConversation(serverId)
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) return markRetry(op, result.error)
        markConversationSynced(op.conversationLocalId)
        pendingOpDao.delete(op.id)
        return OpResult(ok = true)
    }

    private suspend fun processArchive(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val serverId = op.conversationServerId ?: return deleteInvalid(op)
        val result = repository.archiveConversation(serverId)
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) return markRetry(op, result.error)
        markConversationSynced(op.conversationLocalId)
        pendingOpDao.delete(op.id)
        return OpResult(ok = true)
    }

    private suspend fun processDelete(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        val serverId = op.conversationServerId ?: return deleteInvalid(op)
        val result = repository.deleteConversation(serverId)
        if (result.status == 401) return OpResult(unauthorized = true)
        if (!result.ok) return markRetry(op, result.error)
        messageDao.deleteByConversation(op.conversationLocalId)
        conversationDao.deleteByLocalId(op.conversationLocalId)
        pendingOpDao.deleteForConversation(op.conversationLocalId)
        return OpResult(ok = true)
    }

    private suspend fun markConversationSynced(localId: Long) {
        val conv = conversationDao.getByLocalId(localId) ?: return
        conversationDao.update(conv.copy(syncState = ConversationSyncState.SYNCED, lastError = null))
    }

    private suspend fun markRetry(
        op: fr.jarvis.companion.core.database.PendingChatOperationEntity,
        error: String?,
    ): OpResult {
        val delay = minOf(300_000L, 5_000L * (1 shl minOf(op.retryCount, 6)))
        pendingOpDao.update(
            op.copy(
                state = PendingChatOpState.FAILED,
                retryCount = op.retryCount + 1,
                lastError = error,
                nextAttemptAtMillis = System.currentTimeMillis() + delay,
            ),
        )
        return OpResult(error = error)
    }

    private suspend fun deleteInvalid(op: fr.jarvis.companion.core.database.PendingChatOperationEntity): OpResult {
        pendingOpDao.delete(op.id)
        return OpResult(error = "Opération sans serverId")
    }

    data class SyncChatResult(
        val processed: Int = 0,
        val unauthorized: Boolean = false,
        val errors: List<String> = emptyList(),
    )

    private data class OpResult(
        val ok: Boolean = false,
        val unauthorized: Boolean = false,
        val error: String? = null,
    )
}
