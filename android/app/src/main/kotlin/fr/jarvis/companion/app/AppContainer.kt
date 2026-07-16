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
import fr.jarvis.companion.core.sync.SyncManager
import fr.jarvis.companion.data.JarvisRepository

class AppContainer(context: Context) {
    private val appContext = context.applicationContext

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
}

fun Context.appContainer(): AppContainer =
    (applicationContext as JarvisApplication).container
