package fr.jarvis.companion.core.database

object DeliveryState {
    const val LOCAL_PENDING = "LOCAL_PENDING"
    const val QUEUED = "QUEUED"
    const val SENDING = "SENDING"
    const val STREAMING = "STREAMING"
    const val SENT = "SENT"
    const val FAILED_RETRYABLE = "FAILED_RETRYABLE"
    const val FAILED_PERMANENT = "FAILED_PERMANENT"
    const val CANCELLED = "CANCELLED"
}

object PendingChatOpType {
    const val CREATE_CONVERSATION = "CREATE_CONVERSATION"
    const val SEND_MESSAGE = "SEND_MESSAGE"
    const val RENAME = "RENAME"
    const val PIN = "PIN"
    const val UNPIN = "UNPIN"
    const val ARCHIVE = "ARCHIVE"
    const val DELETE = "DELETE"
}

object PendingChatOpState {
    const val PENDING = "pending"
    const val IN_FLIGHT = "in_flight"
    const val DONE = "done"
    const val FAILED = "failed"
}

object ConversationSyncState {
    const val SYNCED = "synced"
    const val PENDING_CREATE = "pending_create"
    const val PENDING_UPDATE = "pending_update"
    const val PENDING_DELETE = "pending_delete"
    const val ERROR = "error"
}
