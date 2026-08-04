package fr.jarvis.companion.data.chat

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.database.ChatMessageEntity
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.database.JarvisDatabase
import fr.jarvis.companion.core.database.PendingChatOpState
import fr.jarvis.companion.core.database.PendingChatOpType
import fr.jarvis.companion.core.database.PendingChatOperationEntity
import fr.jarvis.companion.network.JarvisApiResult
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatSyncRepositoryTest {
    private lateinit var database: JarvisDatabase
    private lateinit var remote: FakeChatSyncRemote
    private lateinit var conversationApplier: RecordingConversationApplier
    private lateinit var responseApplier: RecordingResponseApplier
    private lateinit var sync: ChatSyncRepository

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        database = Room.inMemoryDatabaseBuilder(context, JarvisDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        remote = FakeChatSyncRemote()
        conversationApplier = RecordingConversationApplier()
        responseApplier = RecordingResponseApplier()
        sync = ChatSyncRepository(
            pendingOpDao = database.pendingChatOperationDao(),
            conversationDao = database.chatConversationDao(),
            messageDao = database.chatMessageDao(),
            conversationRepository = conversationApplier,
            chatRepository = responseApplier,
            repository = remote,
        )
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun retryableSendReturnsToQueueThenCompletesWithSameClientRequestId() = runBlocking {
        val seeded = seedPendingMessage("request-stable")
        remote.sendResult = JarvisApiResult.failure("serveur indisponible", status = 503)

        val first = sync.processPendingOperations()

        assertEquals(listOf("serveur indisponible"), first.errors)
        val failed = database.pendingChatOperationDao()
            .getForConversation(seeded.conversationLocalId)
            .single()
        assertEquals(PendingChatOpState.FAILED, failed.state)
        assertEquals(1, failed.retryCount)
        assertTrue(failed.nextAttemptAtMillis > 0)
        assertEquals(
            DeliveryState.FAILED_RETRYABLE,
            database.chatMessageDao().getByLocalId(seeded.userMessageLocalId)?.deliveryState,
        )

        database.pendingChatOperationDao().update(failed.copy(nextAttemptAtMillis = 0))
        remote.sendResult = success(
            JSONObject()
                .put("conversation_id", 91)
                .put("response_text", "Réponse synchronisée"),
        )

        val second = sync.processPendingOperations()

        assertEquals(1, second.processed)
        assertTrue(
            database.pendingChatOperationDao()
                .getForConversation(seeded.conversationLocalId)
                .isEmpty(),
        )
        assertEquals(
            listOf("request-stable", "request-stable"),
            remote.clientMessageIds,
        )
        assertEquals(listOf("request-stable"), responseApplier.clientRequestIds)
    }

    @Test
    fun unauthorizedOperationIsFailedNotStrandedInFlight() = runBlocking {
        val seeded = seedPendingMessage("request-auth")
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.RENAME,
                conversationLocalId = seeded.conversationLocalId,
                conversationServerId = 77,
                payloadJson = """{"title":"Après"}""",
                createdAtMillis = 2,
            ),
        )
        remote.sendResult = JarvisApiResult.failure("réappairage requis", status = 401)

        val result = sync.processPendingOperations()

        assertTrue(result.unauthorized)
        val queued = database.pendingChatOperationDao()
            .getForConversation(seeded.conversationLocalId)
        assertEquals(
            listOf(PendingChatOpState.FAILED, PendingChatOpState.PENDING),
            queued.map { it.state },
        )
        assertEquals("Non autorisé", queued.first().lastError)
    }

    @Test
    fun malformedPayloadReturnsToRetryableStateInsteadOfRemainingInFlight() = runBlocking {
        val conversationLocalId = seedConversation()
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.SEND_MESSAGE,
                conversationLocalId = conversationLocalId,
                conversationServerId = 77,
                payloadJson = "{payload-invalide",
                createdAtMillis = 1,
            ),
        )

        val result = sync.processPendingOperations()

        assertEquals(1, result.errors.size)
        val failed = database.pendingChatOperationDao()
            .getForConversation(conversationLocalId)
            .single()
        assertEquals(PendingChatOpState.FAILED, failed.state)
        assertEquals(1, failed.retryCount)
    }

    @Test
    fun unknownOperationIsRemovedSoItCannotPoisonTheQueue() = runBlocking {
        val conversationLocalId = seedConversation()
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = "future_operation_removed",
                conversationLocalId = conversationLocalId,
                conversationServerId = 77,
                payloadJson = "{}",
                createdAtMillis = 1,
            ),
        )

        val result = sync.processPendingOperations()

        assertEquals(listOf("Type inconnu : future_operation_removed"), result.errors)
        assertTrue(
            database.pendingChatOperationDao()
                .getForConversation(conversationLocalId)
                .isEmpty(),
        )
    }

    private suspend fun seedConversation(): Long = database.chatConversationDao().insert(
        ChatConversationEntity(
            serverId = 77,
            title = "Conversation test",
            createdAtMillis = 1,
            updatedAtMillis = 1,
        ),
    )

    private suspend fun seedPendingMessage(clientRequestId: String): SeededMessage {
        val conversationLocalId = seedConversation()
        val userMessageLocalId = database.chatMessageDao().insert(
            ChatMessageEntity(
                conversationLocalId = conversationLocalId,
                conversationServerId = 77,
                role = "user",
                content = "Bonjour",
                createdAtMillis = 1,
                updatedAtMillis = 1,
                deliveryState = DeliveryState.QUEUED,
                clientRequestId = clientRequestId,
            ),
        )
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.SEND_MESSAGE,
                conversationLocalId = conversationLocalId,
                conversationServerId = 77,
                payloadJson = JSONObject()
                    .put("content", "Bonjour")
                    .put("clientRequestId", clientRequestId)
                    .put("userMessageLocalId", userMessageLocalId)
                    .toString(),
                createdAtMillis = 1,
            ),
        )
        return SeededMessage(conversationLocalId, userMessageLocalId)
    }

    private fun success(json: JSONObject = JSONObject()): JarvisApiResult = JarvisApiResult(
        ok = true,
        status = 200,
        json = json,
        cookie = null,
        error = "",
    )

    private data class SeededMessage(
        val conversationLocalId: Long,
        val userMessageLocalId: Long,
    )

    private class FakeChatSyncRemote : ChatSyncRemote {
        var sendResult: JarvisApiResult = JarvisApiResult.failure("non configuré")
        val clientMessageIds = mutableListOf<String>()

        override suspend fun createMobileConversation(title: String?): JarvisApiResult =
            JarvisApiResult.failure("non utilisé")

        override suspend fun sendMobileChat(
            content: String,
            conversationId: Long?,
            clientMessageId: String?,
        ): JarvisApiResult {
            clientMessageId?.let(clientMessageIds::add)
            return sendResult
        }

        override suspend fun patchConversation(
            id: Long,
            title: String?,
            pinned: Boolean?,
            archived: Boolean?,
        ): JarvisApiResult = JarvisApiResult.failure("non utilisé")

        override suspend fun pinConversation(id: Long): JarvisApiResult =
            JarvisApiResult.failure("non utilisé")

        override suspend fun archiveConversation(id: Long): JarvisApiResult =
            JarvisApiResult.failure("non utilisé")

        override suspend fun deleteConversation(id: Long): JarvisApiResult =
            JarvisApiResult.failure("non utilisé")
    }

    private class RecordingConversationApplier : ConversationSyncApplier {
        override suspend fun applyServerConversationCreated(
            localId: Long,
            serverId: Long,
            title: String?,
        ) = Unit
    }

    private class RecordingResponseApplier : ChatResponseSyncApplier {
        val clientRequestIds = mutableListOf<String>()

        override suspend fun applyHttpChatResponse(
            conversationLocalId: Long,
            userMessageLocalId: Long,
            clientRequestId: String,
            json: JSONObject,
        ) {
            clientRequestIds += clientRequestId
        }
    }
}
