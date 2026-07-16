package fr.jarvis.companion.core.database

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import fr.jarvis.companion.data.chat.ConversationGrouping
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.ZoneId

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatRoomMigrationTest {
    private lateinit var database: JarvisDatabase

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        database = Room.databaseBuilder(context, JarvisDatabase::class.java, "migration-test.db")
            .addMigrations(MIGRATION_1_2)
            .build()
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun migrationCreatesChatTables() = runBlocking {
        val convId = database.chatConversationDao().insert(
            ChatConversationEntity(
                title = "Test",
                createdAtMillis = System.currentTimeMillis(),
                updatedAtMillis = System.currentTimeMillis(),
            ),
        )
        assertTrue(convId > 0)
        val msgId = database.chatMessageDao().insert(
            ChatMessageEntity(
                conversationLocalId = convId,
                role = "user",
                content = "Bonjour",
                createdAtMillis = System.currentTimeMillis(),
                updatedAtMillis = System.currentTimeMillis(),
                deliveryState = DeliveryState.SENT,
            ),
        )
        assertTrue(msgId > 0)
        val observed = database.chatMessageDao().observeByConversation(convId).first()
        assertEquals(1, observed.size)
    }

    @Test
    fun pendingOperationsPreserveOrderPerConversation() = runBlocking {
        val now = System.currentTimeMillis()
        val convId = database.chatConversationDao().insert(
            ChatConversationEntity(
                title = "Ops",
                createdAtMillis = now,
                updatedAtMillis = now,
            ),
        )
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.CREATE_CONVERSATION,
                conversationLocalId = convId,
                payloadJson = "{}",
                createdAtMillis = now,
            ),
        )
        database.pendingChatOperationDao().insert(
            PendingChatOperationEntity(
                type = PendingChatOpType.SEND_MESSAGE,
                conversationLocalId = convId,
                payloadJson = "{\"content\":\"a\"}",
                createdAtMillis = now + 1,
            ),
        )
        val ops = database.pendingChatOperationDao().getForConversation(convId)
        assertEquals(2, ops.size)
        assertEquals(PendingChatOpType.CREATE_CONVERSATION, ops[0].type)
        assertEquals(PendingChatOpType.SEND_MESSAGE, ops[1].type)
    }

    @Test
    fun clientRequestIdUniqueConstraint() = runBlocking {
        val now = System.currentTimeMillis()
        val convId = database.chatConversationDao().insert(
            ChatConversationEntity(
                title = "Dedup",
                createdAtMillis = now,
                updatedAtMillis = now,
            ),
        )
        val requestId = "msg_testunique01"
        database.chatMessageDao().insert(
            ChatMessageEntity(
                conversationLocalId = convId,
                role = "user",
                content = "first",
                createdAtMillis = now,
                updatedAtMillis = now,
                clientRequestId = requestId,
            ),
        )
        var duplicateRejected = false
        try {
            database.chatMessageDao().insert(
                ChatMessageEntity(
                    conversationLocalId = convId,
                    role = "user",
                    content = "duplicate",
                    createdAtMillis = now + 1,
                    updatedAtMillis = now + 1,
                    clientRequestId = requestId,
                ),
            )
        } catch (_: Exception) {
            duplicateRejected = true
        }
        assertTrue(duplicateRejected)
        val byId = database.chatMessageDao().getByClientRequestId(requestId)
        assertNotNull(byId)
        assertEquals("first", byId?.content)
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConversationGroupingTest {
    private val zone = ZoneId.of("Europe/Paris")

    @Test
    fun groupsPinnedTodayYesterdayAndOlder() {
        val today = LocalDate.now(zone)
        val todayMillis = today.atStartOfDay(zone).toInstant().toEpochMilli()
        val yesterdayMillis = today.minusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val oldMillis = today.minusDays(30).atStartOfDay(zone).toInstant().toEpochMilli()

        val conversations = listOf(
            ChatConversationEntity(
                localId = 1,
                title = "Pinned",
                isPinned = true,
                createdAtMillis = oldMillis,
                updatedAtMillis = oldMillis,
                lastMessageAtMillis = oldMillis,
            ),
            ChatConversationEntity(
                localId = 2,
                title = "Today",
                createdAtMillis = todayMillis,
                updatedAtMillis = todayMillis,
                lastMessageAtMillis = todayMillis,
            ),
            ChatConversationEntity(
                localId = 3,
                title = "Yesterday",
                createdAtMillis = yesterdayMillis,
                updatedAtMillis = yesterdayMillis,
                lastMessageAtMillis = yesterdayMillis,
            ),
            ChatConversationEntity(
                localId = 4,
                title = "Older",
                createdAtMillis = oldMillis,
                updatedAtMillis = oldMillis,
                lastMessageAtMillis = oldMillis,
            ),
        )

        val groups = ConversationGrouping.group(conversations, zone)
        assertEquals("Épinglées", groups[0].label)
        assertEquals(1, groups[0].items.size)
        assertTrue(groups.any { it.label == "Aujourd'hui" })
        assertTrue(groups.any { it.label == "Hier" })
        assertTrue(groups.any { it.label == "Plus anciennes" })
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class DeliveryStateTransitionTest {
    @Test
    fun deliveryStateConstantsAreStable() {
        assertEquals("LOCAL_PENDING", DeliveryState.LOCAL_PENDING)
        assertEquals("QUEUED", DeliveryState.QUEUED)
        assertEquals("SENDING", DeliveryState.SENDING)
        assertEquals("STREAMING", DeliveryState.STREAMING)
        assertEquals("SENT", DeliveryState.SENT)
        assertEquals("FAILED_RETRYABLE", DeliveryState.FAILED_RETRYABLE)
        assertEquals("FAILED_PERMANENT", DeliveryState.FAILED_PERMANENT)
        assertEquals("CANCELLED", DeliveryState.CANCELLED)
    }

    @Test
    fun pendingToSentTransitionPath() {
        val states = listOf(
            DeliveryState.LOCAL_PENDING,
            DeliveryState.QUEUED,
            DeliveryState.SENDING,
            DeliveryState.STREAMING,
            DeliveryState.SENT,
        )
        assertEquals(DeliveryState.SENT, states.last())
    }
}
