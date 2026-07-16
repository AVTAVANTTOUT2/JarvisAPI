package fr.jarvis.companion.app

import android.content.Context
import fr.jarvis.companion.core.connectivity.ConnectivityObserver
import fr.jarvis.companion.core.database.JarvisDatabase
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
}

fun Context.appContainer(): AppContainer =
    (applicationContext as JarvisApplication).container
