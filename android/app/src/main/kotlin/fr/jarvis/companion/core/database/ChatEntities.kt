package fr.jarvis.companion.core.database

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "chat_conversations",
    indices = [
        Index(value = ["serverId"]),
        Index(value = ["lastMessageAtMillis"]),
        Index(value = ["isPinned", "isArchived"]),
    ],
)
data class ChatConversationEntity(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val serverId: Long? = null,
    val title: String = "",
    val isPinned: Boolean = false,
    val isArchived: Boolean = false,
    val createdAtMillis: Long,
    val updatedAtMillis: Long,
    val lastMessageAtMillis: Long? = null,
    val lastMessagePreview: String? = null,
    val syncState: String = ConversationSyncState.SYNCED,
    val lastError: String? = null,
    val pendingDeletion: Boolean = false,
)

@Entity(
    tableName = "chat_messages",
    indices = [
        Index(value = ["conversationLocalId"]),
        Index(value = ["deliveryState"]),
        Index(value = ["clientRequestId"], unique = true),
        Index(value = ["conversationServerId"]),
    ],
)
data class ChatMessageEntity(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val serverId: Long? = null,
    val conversationLocalId: Long,
    val conversationServerId: Long? = null,
    val role: String,
    val content: String,
    val createdAtMillis: Long,
    val updatedAtMillis: Long,
    val deliveryState: String = DeliveryState.SENT,
    val clientRequestId: String? = null,
    val errorMessage: String? = null,
    val isStreaming: Boolean = false,
)

@Entity(
    tableName = "pending_chat_operations",
    indices = [
        Index(value = ["conversationLocalId", "createdAtMillis"]),
        Index(value = ["state", "nextAttemptAtMillis"]),
    ],
)
data class PendingChatOperationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val type: String,
    val conversationLocalId: Long,
    val conversationServerId: Long? = null,
    val payloadJson: String,
    val createdAtMillis: Long,
    val retryCount: Int = 0,
    val nextAttemptAtMillis: Long = 0,
    val lastError: String? = null,
    val state: String = PendingChatOpState.PENDING,
)

@Entity(tableName = "chat_drafts")
data class ChatDraftEntity(
    @PrimaryKey val conversationLocalId: Long,
    val draftText: String = "",
    val updatedAtMillis: Long,
)
