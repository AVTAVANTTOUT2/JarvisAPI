package fr.jarvis.companion.core.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import fr.jarvis.companion.app.appContainer
import java.util.concurrent.TimeUnit

class LocationSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val container = applicationContext.appContainer()
        val outcome = container.locationSyncCoordinator.syncOnce(id.toString())
        return when {
            outcome.lockNotAcquired -> Result.retry()
            outcome.unauthorized -> Result.success()
            outcome.skippedNoToken -> Result.success()
            outcome.error != null -> Result.retry()
            else -> Result.success()
        }
    }

    companion object {
        const val WORK_NAME = "jarvis-location-sync"
        // REPLACE : KEEP bloquait le sync live si un one-shot précédent était encore en attente.
        internal val IMMEDIATE_WORK_POLICY: ExistingWorkPolicy = ExistingWorkPolicy.REPLACE

        fun schedule(context: Context) {
            val appContext = context.applicationContext
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val periodic = PeriodicWorkRequestBuilder<LocationSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(appContext).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                periodic,
            )
        }

        fun enqueueNow(context: Context) {
            val appContext = context.applicationContext
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val oneShot = OneTimeWorkRequestBuilder<LocationSyncWorker>()
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(appContext).enqueueUniqueWork(
                "${WORK_NAME}-now",
                IMMEDIATE_WORK_POLICY,
                oneShot,
            )
        }
    }
}
