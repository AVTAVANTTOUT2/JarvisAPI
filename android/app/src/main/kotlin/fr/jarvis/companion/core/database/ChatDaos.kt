package fr.jarvis.companion.core.database

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface ChatConversationDao {
    @Query("SELECT * FROM chat_conversations WHERE pendingDeletion = 0 ORDER BY isPinned DESC, lastMessageAtMillis DESC")
    fun observeActive(): Flow<List<ChatConversationEntity>>

    @Query("SELECT * FROM chat_conversations WHERE localId = :localId LIMIT 1")
    fun observeByLocalId(localId: Long): Flow<ChatConversationEntity?>

    @Query("SELECT * FROM chat_conversations WHERE localId = :localId LIMIT 1")
    suspend fun getByLocalId(localId: Long): ChatConversationEntity?

    @Query("SELECT * FROM chat_conversations WHERE serverId = :serverId LIMIT 1")
    suspend fun getByServerId(serverId: Long): ChatConversationEntity?

    @Query(
        """
        SELECT * FROM chat_conversations
        WHERE pendingDeletion = 0 AND isArchived = 0
        AND (title LIKE '%' || :query || '%' OR lastMessagePreview LIKE '%' || :query || '%')
        ORDER BY isPinned DESC, lastMessageAtMillis DESC
        """,
    )
    fun search(query: String): Flow<List<ChatConversationEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: ChatConversationEntity): Long

    @Update
    suspend fun update(entity: ChatConversationEntity)

    @Query("DELETE FROM chat_conversations WHERE localId = :localId")
    suspend fun deleteByLocalId(localId: Long)

    @Query("UPDATE chat_conversations SET pendingDeletion = 1, syncState = :syncState WHERE localId = :localId")
    suspend fun markPendingDeletion(localId: Long, syncState: String)

    @Query(
        """
        UPDATE chat_conversations
        SET lastMessageAtMillis = :atMillis, lastMessagePreview = :preview, updatedAtMillis = :atMillis
        WHERE localId = :localId
        """,
    )
    suspend fun updateLastMessage(localId: Long, atMillis: Long, preview: String?)
}

@Dao
interface ChatMessageDao {
    @Query("SELECT * FROM chat_messages WHERE conversationLocalId = :conversationLocalId ORDER BY createdAtMillis ASC")
    fun observeByConversation(conversationLocalId: Long): Flow<List<ChatMessageEntity>>

    @Query("SELECT * FROM chat_messages WHERE localId = :localId LIMIT 1")
    suspend fun getByLocalId(localId: Long): ChatMessageEntity?

    @Query("SELECT * FROM chat_messages WHERE clientRequestId = :clientRequestId LIMIT 1")
    suspend fun getByClientRequestId(clientRequestId: String): ChatMessageEntity?

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(entity: ChatMessageEntity): Long

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: ChatMessageEntity): Long

    @Update
    suspend fun update(entity: ChatMessageEntity)

    @Query("UPDATE chat_messages SET content = :content, updatedAtMillis = :now, isStreaming = :streaming WHERE localId = :localId")
    suspend fun updateStreamingContent(localId: Long, content: String, now: Long, streaming: Boolean)

    @Query(
        """
        UPDATE chat_messages
        SET deliveryState = :state, errorMessage = :error, updatedAtMillis = :now, isStreaming = 0
        WHERE localId = :localId
        """,
    )
    suspend fun updateDeliveryState(localId: Long, state: String, error: String?, now: Long)

    @Query("DELETE FROM chat_messages WHERE conversationLocalId = :conversationLocalId")
    suspend fun deleteByConversation(conversationLocalId: Long)

    @Query("SELECT COUNT(*) FROM chat_messages WHERE conversationLocalId = :conversationLocalId")
    suspend fun countByConversation(conversationLocalId: Long): Int
}

@Dao
interface PendingChatOperationDao {
    @Query(
        """
        SELECT * FROM pending_chat_operations
        WHERE state IN ('pending', 'failed')
        AND nextAttemptAtMillis <= :now
        ORDER BY conversationLocalId ASC, createdAtMillis ASC
        """,
    )
    suspend fun getReady(now: Long): List<PendingChatOperationEntity>

    @Query(
        """
        SELECT * FROM pending_chat_operations
        WHERE conversationLocalId = :conversationLocalId AND state IN ('pending', 'failed', 'in_flight')
        ORDER BY createdAtMillis ASC
        """,
    )
    suspend fun getForConversation(conversationLocalId: Long): List<PendingChatOperationEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: PendingChatOperationEntity): Long

    @Update
    suspend fun update(entity: PendingChatOperationEntity)

    @Query("DELETE FROM pending_chat_operations WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("DELETE FROM pending_chat_operations WHERE conversationLocalId = :conversationLocalId")
    suspend fun deleteForConversation(conversationLocalId: Long)
}

@Dao
interface ChatDraftDao {
    @Query("SELECT * FROM chat_drafts WHERE conversationLocalId = :conversationLocalId LIMIT 1")
    fun observe(conversationLocalId: Long): Flow<ChatDraftEntity?>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: ChatDraftEntity)

    @Query("DELETE FROM chat_drafts WHERE conversationLocalId = :conversationLocalId")
    suspend fun delete(conversationLocalId: Long)
}
