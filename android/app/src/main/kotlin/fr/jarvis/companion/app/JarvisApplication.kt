package fr.jarvis.companion.app

import android.app.Application
import android.os.Build
import fr.jarvis.companion.core.sync.LocationSyncWorker
import fr.jarvis.companion.core.sync.SyncWorker

class JarvisApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.connectivityObserver.start()
        if (!isRobolectricUnitTest()) {
            SyncWorker.schedule(this)
            LocationSyncWorker.schedule(this)
        }
    }

    private fun isRobolectricUnitTest(): Boolean =
        Build.FINGERPRINT.contains("robolectric", ignoreCase = true) ||
            Build.MANUFACTURER.equals("unknown", ignoreCase = true) &&
            Build.BRAND.equals("generic", ignoreCase = true)
}
