package fr.jarvis.companion.app

import android.content.Context
import fr.jarvis.companion.core.connectivity.ConnectivityObserver
import fr.jarvis.companion.core.database.JarvisDatabase
import fr.jarvis.companion.core.network.JarvisChatWebSocket
import fr.jarvis.companion.core.sync.SyncManager
import fr.jarvis.companion.data.JarvisRepository
import fr.jarvis.companion.data.chat.ChatRepository
import fr.jarvis.companion.data.chat.ChatSyncRepository
import fr.jarvis.companion.data.chat.ConversationRepository

class AppContainer(context: Context) {
    val appContext: Context = context.applicationContext

    val database: JarvisDatabase = JarvisDatabase.getInstance(appContext)
    val repository: JarvisRepository = JarvisRepository(appContext)
    val connectivityObserver: ConnectivityObserver = ConnectivityObserver(appContext)
    val syncManager: SyncManager = SyncManager(
        context = appContext,
        database = database,
        repository = repository,
        connectivityObserver = connectivityObserver,
    )

    val chatWebSocket: JarvisChatWebSocket = JarvisChatWebSocket(appContext)

    val conversationRepository: ConversationRepository = ConversationRepository(
        conversationDao = database.chatConversationDao(),
        pendingOpDao = database.pendingChatOperationDao(),
        repository = repository,
    )

    val chatRepository: ChatRepository = ChatRepository(
        messageDao = database.chatMessageDao(),
        conversationDao = database.chatConversationDao(),
        draftDao = database.chatDraftDao(),
        pendingOpDao = database.pendingChatOperationDao(),
        repository = repository,
        webSocket = chatWebSocket,
    )

    val chatSyncRepository: ChatSyncRepository = ChatSyncRepository(
        pendingOpDao = database.pendingChatOperationDao(),
        conversationDao = database.chatConversationDao(),
        messageDao = database.chatMessageDao(),
        conversationRepository = conversationRepository,
        chatRepository = chatRepository,
        repository = repository,
    )
}

fun Context.appContainer(): AppContainer =
    (applicationContext as JarvisApplication).container
