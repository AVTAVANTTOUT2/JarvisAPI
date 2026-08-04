package fr.jarvis.companion.data.chat

import fr.jarvis.companion.network.JarvisApiResult
import org.json.JSONObject

interface ChatSyncRemote {
    suspend fun createMobileConversation(title: String? = null): JarvisApiResult

    suspend fun sendMobileChat(
        content: String,
        conversationId: Long? = null,
        clientMessageId: String? = null,
    ): JarvisApiResult

    suspend fun patchConversation(
        id: Long,
        title: String? = null,
        pinned: Boolean? = null,
        archived: Boolean? = null,
    ): JarvisApiResult

    suspend fun pinConversation(id: Long): JarvisApiResult

    suspend fun archiveConversation(id: Long): JarvisApiResult

    suspend fun deleteConversation(id: Long): JarvisApiResult
}

interface ConversationSyncApplier {
    suspend fun applyServerConversationCreated(
        localId: Long,
        serverId: Long,
        title: String?,
    )
}

interface ChatResponseSyncApplier {
    suspend fun applyHttpChatResponse(
        conversationLocalId: Long,
        userMessageLocalId: Long,
        clientRequestId: String,
        json: JSONObject,
    )
}
