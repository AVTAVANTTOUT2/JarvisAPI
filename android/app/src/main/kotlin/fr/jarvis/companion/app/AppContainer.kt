package fr.jarvis.companion.app

import android.content.Context
import fr.jarvis.companion.core.connectivity.ConnectivityObserver
import fr.jarvis.companion.core.database.JarvisDatabase
import fr.jarvis.companion.core.location.AdaptiveLocationPolicy
import fr.jarvis.companion.core.location.LocationDeduplicator
import fr.jarvis.companion.core.location.LocationEngine
import fr.jarvis.companion.core.location.LocationManagerEngine
import fr.jarvis.companion.core.location.LocationSyncCoordinator
import fr.jarvis.companion.core.location.LocationValidator
import fr.jarvis.companion.core.location.PendingLocationStore
import fr.jarvis.companion.core.location.SyncFingerprintCache
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

    val syncFingerprintCache: SyncFingerprintCache = SyncFingerprintCache()
    val locationEngine: LocationEngine = LocationManagerEngine(appContext)
    val adaptiveLocationPolicy: AdaptiveLocationPolicy = AdaptiveLocationPolicy()
    val locationValidator: LocationValidator = LocationValidator()
    val locationDeduplicator: LocationDeduplicator = LocationDeduplicator(syncFingerprintCache)
    val pendingLocationStore: PendingLocationStore = PendingLocationStore(database)
    val locationSyncCoordinator: LocationSyncCoordinator = LocationSyncCoordinator(
        context = appContext,
        store = pendingLocationStore,
        repository = repository,
        deduplicator = locationDeduplicator,
        syncMetadataDao = database.syncMetadataDao(),
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
