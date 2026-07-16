package fr.jarvis.companion.data.chat

import com.google.gson.Gson
import com.google.gson.JsonObject
import fr.jarvis.companion.core.database.ChatConversationDao
import fr.jarvis.companion.core.database.ChatDraftDao
import fr.jarvis.companion.core.database.ChatDraftEntity
import fr.jarvis.companion.core.database.ChatMessageDao
import fr.jarvis.companion.core.database.ChatMessageEntity
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.database.PendingChatOpType
import fr.jarvis.companion.core.database.PendingChatOperationDao
import fr.jarvis.companion.core.database.PendingChatOperationEntity
import fr.jarvis.companion.core.network.ChatWebSocketListener
import fr.jarvis.companion.core.network.JarvisChatWebSocket
import fr.jarvis.companion.core.network.WsConnectionState
import fr.jarvis.companion.core.network.WsIncomingMessage
import fr.jarvis.companion.data.JarvisRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class PendingActionState(
    val action: JsonObject,
    val actionType: String?,
    val message: String?,
)

class ChatRepository(
    private val messageDao: ChatMessageDao,
    private val conversationDao: ChatConversationDao,
    private val draftDao: ChatDraftDao,
    private val pendingOpDao: PendingChatOperationDao,
    private val repository: JarvisRepository,
    private val webSocket: JarvisChatWebSocket,
    private val gson: Gson = Gson(),
) : ChatWebSocketListener {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val streamMutex = Mutex()
    private var streamLocalId: Long? = null
    private var streamBuffer = StringBuilder()
    private var flushJob: Job? = null

    private val _pendingAction = MutableStateFlow<PendingActionState?>(null)
    val pendingAction: StateFlow<PendingActionState?> = _pendingAction.asStateFlow()

    private var activeConversationLocalId: Long? = null

    init {
        webSocket.setListener(this)
    }

    val wsConnectionState: StateFlow<WsConnectionState> = webSocket.connectionState

    fun observeMessages(conversationLocalId: Long): Flow<List<ChatMessageEntity>> =
        messageDao.observeByConversation(conversationLocalId)

    fun observeDraft(conversationLocalId: Long): Flow<ChatDraftEntity?> =
        draftDao.observe(conversationLocalId)

    suspend fun saveDraft(conversationLocalId: Long, text: String) = withContext(Dispatchers.IO) {
        draftDao.upsert(
            ChatDraftEntity(
                conversationLocalId = conversationLocalId,
                draftText = text,
                updatedAtMillis = System.currentTimeMillis(),
            ),
        )
    }

    suspend fun clearDraft(conversationLocalId: Long) = withContext(Dispatchers.IO) {
        draftDao.delete(conversationLocalId)
    }

    fun openConversation(conversationLocalId: Long, conversationServerId: Long?) {
        activeConversationLocalId = conversationLocalId
        webSocket.connect(conversationServerId)
    }

    fun closeConversation() {
        activeConversationLocalId = null
        webSocket.disconnect()
        _pendingAction.value = null
    }

    suspend fun refreshMessages(conversationLocalId: Long) = withContext(Dispatchers.IO) {
        val conv = conversationDao.getByLocalId(conversationLocalId) ?: return@withContext
        val serverId = conv.serverId ?: return@withContext
        val result = repository.getConversationDetail(serverId)
        if (!result.ok) return@withContext
        mergeServerMessages(conversationLocalId, serverId, result.json)
    }

    suspend fun sendMessage(conversationLocalId: Long, content: String): SendResult =
        withContext(Dispatchers.IO) {
            val trimmed = content.trim()
            if (trimmed.isEmpty()) return@withContext SendResult(empty = true)

            val conv = conversationDao.getByLocalId(conversationLocalId)
                ?: return@withContext SendResult(error = "Conversation introuvable")

            val now = System.currentTimeMillis()
            val clientRequestId = "msg_${UUID.randomUUID().toString().replace("-", "")}"

            val userMsgId = messageDao.insert(
                ChatMessageEntity(
                    conversationLocalId = conversationLocalId,
                    conversationServerId = conv.serverId,
                    role = "user",
                    content = trimmed,
                    createdAtMillis = now,
                    updatedAtMillis = now,
                    deliveryState = DeliveryState.LOCAL_PENDING,
                    clientRequestId = clientRequestId,
                ),
            )
            conversationDao.updateLastMessage(conversationLocalId, now, trimmed.take(120))

            val wsState = webSocket.connectionState.value
            if (wsState == WsConnectionState.Connected && conv.serverId != null) {
                messageDao.updateDeliveryState(userMsgId, DeliveryState.SENDING, null, now)
                val assistantId = startAssistantPlaceholder(conversationLocalId, conv.serverId, now)
                val sent = webSocket.sendText(trimmed)
                if (sent) {
                    messageDao.updateDeliveryState(userMsgId, DeliveryState.SENT, null, System.currentTimeMillis())
                    return@withContext SendResult(
                        userMessageLocalId = userMsgId,
                        assistantMessageLocalId = assistantId,
                        viaWebSocket = true,
                    )
                }
            }

            messageDao.updateDeliveryState(userMsgId, DeliveryState.QUEUED, null, now)
            pendingOpDao.insert(
                PendingChatOperationEntity(
                    type = PendingChatOpType.SEND_MESSAGE,
                    conversationLocalId = conversationLocalId,
                    conversationServerId = conv.serverId,
                    payloadJson = gson.toJson(
                        mapOf(
                            "content" to trimmed,
                            "clientRequestId" to clientRequestId,
                            "userMessageLocalId" to userMsgId,
                        ),
                    ),
                    createdAtMillis = now,
                ),
            )
            SendResult(userMessageLocalId = userMsgId, queued = true)
        }

    suspend fun cancelPendingMessage(localId: Long) = withContext(Dispatchers.IO) {
        val msg = messageDao.getByLocalId(localId) ?: return@withContext
        if (msg.deliveryState in setOf(DeliveryState.QUEUED, DeliveryState.LOCAL_PENDING, DeliveryState.FAILED_RETRYABLE)) {
            messageDao.updateDeliveryState(localId, DeliveryState.CANCELLED, null, System.currentTimeMillis())
        }
    }

    suspend fun confirmPendingAction(confirmed: Boolean) = withContext(Dispatchers.IO) {
        val pending = _pendingAction.value ?: return@withContext
        val conv = activeConversationLocalId?.let { conversationDao.getByLocalId(it) }
        val serverId = conv?.serverId
        if (serverId != null) {
            if (webSocket.connectionState.value == WsConnectionState.Connected) {
                webSocket.sendActionConfirm(pending.action, confirmed)
            } else {
                repository.confirmMobileChat(serverId, confirmed)
            }
        }
        _pendingAction.value = null
    }

    override fun onWsMessage(message: WsIncomingMessage) {
        scope.launch { handleWsMessage(message) }
    }

    override fun onWsConnectionState(state: WsConnectionState) {
        // Exposed via connectionState flow
    }

    private suspend fun handleWsMessage(message: WsIncomingMessage) {
        val localId = activeConversationLocalId ?: return
        when (message.type) {
            "chunk" -> appendStreamChunk(localId, message.content.orEmpty())
            "done", "response" -> finalizeStream(localId, message.content)
            "response_followup" -> appendFollowup(localId, message.content.orEmpty())
            "action_pending" -> {
                _pendingAction.value = PendingActionState(
                    action = message.action ?: JsonObject(),
                    actionType = message.actionType,
                    message = message.message ?: message.content,
                )
            }
            "error" -> handleStreamError(localId, message.message ?: "Erreur serveur")
            "conversation_switched" -> {
                message.conversationId?.let { serverId ->
                    val conv = conversationDao.getByLocalId(localId) ?: return@let
                    if (conv.serverId != serverId) {
                        conversationDao.update(conv.copy(serverId = serverId, updatedAtMillis = System.currentTimeMillis()))
                    }
                }
            }
        }
    }

    private suspend fun startAssistantPlaceholder(
        conversationLocalId: Long,
        conversationServerId: Long?,
        now: Long,
    ): Long {
        return messageDao.insert(
            ChatMessageEntity(
                conversationLocalId = conversationLocalId,
                conversationServerId = conversationServerId,
                role = "assistant",
                content = "",
                createdAtMillis = now + 1,
                updatedAtMillis = now + 1,
                deliveryState = DeliveryState.STREAMING,
                isStreaming = true,
            ),
        )
    }

    private suspend fun appendStreamChunk(conversationLocalId: Long, delta: String) {
        streamMutex.withLock {
            if (streamLocalId == null) {
                val now = System.currentTimeMillis()
                streamLocalId = startAssistantPlaceholder(conversationLocalId, null, now)
                streamBuffer.clear()
            }
            streamBuffer.append(delta)
            scheduleFlush()
        }
    }

    private fun scheduleFlush() {
        flushJob?.cancel()
        flushJob = scope.launch {
            delay(STREAM_FLUSH_MS)
            flushStreamBuffer()
        }
    }

    private suspend fun flushStreamBuffer() {
        streamMutex.withLock {
            val id = streamLocalId ?: return
            val content = streamBuffer.toString()
            messageDao.updateStreamingContent(id, content, System.currentTimeMillis(), streaming = true)
        }
    }

    private suspend fun finalizeStream(conversationLocalId: Long, content: String?) {
        streamMutex.withLock {
            flushJob?.cancel()
            val id = streamLocalId
            val finalContent = content?.takeIf { it.isNotBlank() } ?: streamBuffer.toString()
            streamBuffer.clear()
            streamLocalId = null
            val now = System.currentTimeMillis()
            if (id != null) {
                messageDao.updateStreamingContent(id, finalContent, now, streaming = false)
                messageDao.updateDeliveryState(id, DeliveryState.SENT, null, now)
            } else if (finalContent.isNotBlank()) {
                messageDao.insert(
                    ChatMessageEntity(
                        conversationLocalId = conversationLocalId,
                        role = "assistant",
                        content = finalContent,
                        createdAtMillis = now,
                        updatedAtMillis = now,
                        deliveryState = DeliveryState.SENT,
                    ),
                )
            }
            conversationDao.updateLastMessage(conversationLocalId, now, finalContent.take(120))
        }
    }

    private suspend fun appendFollowup(conversationLocalId: Long, content: String) {
        if (content.isBlank()) return
        val now = System.currentTimeMillis()
        messageDao.insert(
            ChatMessageEntity(
                conversationLocalId = conversationLocalId,
                role = "assistant",
                content = content,
                createdAtMillis = now,
                updatedAtMillis = now,
                deliveryState = DeliveryState.SENT,
            ),
        )
        conversationDao.updateLastMessage(conversationLocalId, now, content.take(120))
    }

    private suspend fun handleStreamError(conversationLocalId: Long, error: String) {
        streamMutex.withLock {
            flushJob?.cancel()
            val id = streamLocalId
            streamLocalId = null
            streamBuffer.clear()
            if (id != null) {
                messageDao.updateDeliveryState(id, DeliveryState.FAILED_RETRYABLE, error, System.currentTimeMillis())
            }
        }
    }

    suspend fun applyHttpChatResponse(
        conversationLocalId: Long,
        userMessageLocalId: Long,
        clientRequestId: String,
        json: JSONObject,
    ) = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        messageDao.updateDeliveryState(userMessageLocalId, DeliveryState.SENT, null, now)

        val serverConvId = json.optLong("conversation_id", -1).takeIf { it > 0 }
        serverConvId?.let { sid ->
            val conv = conversationDao.getByLocalId(conversationLocalId)
            if (conv != null && conv.serverId != sid) {
                conversationDao.update(conv.copy(serverId = sid, updatedAtMillis = now))
            }
        }

        val responseText = json.optString("response_text", "")
        if (responseText.isNotBlank()) {
            messageDao.insert(
                ChatMessageEntity(
                    conversationLocalId = conversationLocalId,
                    conversationServerId = serverConvId,
                    role = "assistant",
                    content = responseText,
                    createdAtMillis = now + 1,
                    updatedAtMillis = now + 1,
                    deliveryState = DeliveryState.SENT,
                ),
            )
            conversationDao.updateLastMessage(conversationLocalId, now, responseText.take(120))
        }

        if (json.optBoolean("needs_confirmation", false)) {
            val action = json.optJSONObject("action")
            if (action != null) {
                _pendingAction.value = PendingActionState(
                    action = gson.fromJson(action.toString(), JsonObject::class.java),
                    actionType = action.optString("type"),
                    message = responseText,
                )
            }
        }
    }

    private suspend fun mergeServerMessages(
        conversationLocalId: Long,
        serverId: Long,
        json: JSONObject,
    ) {
        val array = json.optJSONArray("messages") ?: JSONArray()
        val now = System.currentTimeMillis()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            val role = item.optString("role", "assistant")
            val content = item.optString("content", "")
            if (content.isBlank()) continue
            val serverMsgId = item.optLong("id", -1).takeIf { it > 0 }
            val createdAt = parseCreatedAt(item.optString("created_at")) ?: now
            messageDao.upsert(
                ChatMessageEntity(
                    serverId = serverMsgId,
                    conversationLocalId = conversationLocalId,
                    conversationServerId = serverId,
                    role = role,
                    content = content,
                    createdAtMillis = createdAt,
                    updatedAtMillis = now,
                    deliveryState = DeliveryState.SENT,
                ),
            )
        }
    }

    private fun parseCreatedAt(iso: String?): Long? {
        if (iso.isNullOrBlank()) return null
        return runCatching { java.time.Instant.parse(iso).toEpochMilli() }.getOrNull()
    }

    data class SendResult(
        val userMessageLocalId: Long? = null,
        val assistantMessageLocalId: Long? = null,
        val viaWebSocket: Boolean = false,
        val queued: Boolean = false,
        val empty: Boolean = false,
        val error: String? = null,
    )

    companion object {
        private const val STREAM_FLUSH_MS = 150L
    }
}
