package fr.jarvis.companion.core.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import fr.jarvis.companion.app.appContainer
import java.util.concurrent.TimeUnit

class ChatSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val container = applicationContext.appContainer()
        val syncResult = container.chatSyncRepository.processPendingOperations()
        container.conversationRepository.refreshFromServer()
        return when {
            syncResult.unauthorized -> Result.success()
            syncResult.errors.isNotEmpty() -> Result.retry()
            else -> Result.success()
        }
    }

    companion object {
        private const val WORK_NAME = "jarvis_chat_sync"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val request = PeriodicWorkRequestBuilder<ChatSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }

        fun runOnce(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val request = OneTimeWorkRequestBuilder<ChatSyncWorker>()
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(context.applicationContext).enqueue(request)
        }
    }
}
