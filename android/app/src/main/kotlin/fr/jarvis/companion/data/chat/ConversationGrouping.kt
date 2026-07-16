package fr.jarvis.companion.data.chat

import fr.jarvis.companion.core.database.ChatConversationEntity
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

enum class ConversationDateGroup {
    PINNED,
    TODAY,
    YESTERDAY,
    LAST_7_DAYS,
    OLDER,
}

data class GroupedConversations(
    val group: ConversationDateGroup,
    val label: String,
    val items: List<ChatConversationEntity>,
)

object ConversationGrouping {
    fun group(
        conversations: List<ChatConversationEntity>,
        zoneId: ZoneId = ZoneId.systemDefault(),
    ): List<GroupedConversations> {
        val now = LocalDate.now(zoneId)
        val yesterday = now.minusDays(1)
        val weekAgo = now.minusDays(7)

        val pinned = conversations.filter { it.isPinned && !it.isArchived }
        val unpinned = conversations.filter { !it.isPinned && !it.isArchived }

        fun dateOf(conv: ChatConversationEntity): LocalDate? {
            val millis = conv.lastMessageAtMillis ?: conv.createdAtMillis
            return Instant.ofEpochMilli(millis).atZone(zoneId).toLocalDate()
        }

        val today = mutableListOf<ChatConversationEntity>()
        val yday = mutableListOf<ChatConversationEntity>()
        val last7 = mutableListOf<ChatConversationEntity>()
        val older = mutableListOf<ChatConversationEntity>()

        for (conv in unpinned) {
            val date = dateOf(conv)
            if (date == null) {
                older.add(conv)
            } else when {
                date == now -> today.add(conv)
                date == yesterday -> yday.add(conv)
                date.isAfter(weekAgo) -> last7.add(conv)
                else -> older.add(conv)
            }
        }

        val result = mutableListOf<GroupedConversations>()
        if (pinned.isNotEmpty()) {
            result.add(GroupedConversations(ConversationDateGroup.PINNED, "Épinglées", pinned))
        }
        if (today.isNotEmpty()) {
            result.add(GroupedConversations(ConversationDateGroup.TODAY, "Aujourd'hui", today))
        }
        if (yday.isNotEmpty()) {
            result.add(GroupedConversations(ConversationDateGroup.YESTERDAY, "Hier", yday))
        }
        if (last7.isNotEmpty()) {
            result.add(GroupedConversations(ConversationDateGroup.LAST_7_DAYS, "7 derniers jours", last7))
        }
        if (older.isNotEmpty()) {
            result.add(GroupedConversations(ConversationDateGroup.OLDER, "Plus anciennes", older))
        }
        return result
    }
}
