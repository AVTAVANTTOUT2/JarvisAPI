package fr.jarvis.companion.receivers

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import fr.jarvis.companion.core.sync.LocationSyncWorker
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import fr.jarvis.companion.services.JarvisLocationService

/** Restaure GPS et sync location après redémarrage. */
class JarvisBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (!isSupportedBootAction(intent?.action)) return
        JarvisNotifications.createChannels(context)

        val plan = bootRecoveryPlan(
            hasNativeToken = JarvisSettings.nativeToken(context).isNotEmpty(),
            locationEnabled = JarvisSettings.isLocationEnabled(context),
            hasLocationPermission = context.checkSelfPermission(
                Manifest.permission.ACCESS_FINE_LOCATION,
            ) == PackageManager.PERMISSION_GRANTED,
            wakeWordEnabled = JarvisSettings.isWakeWordEnabled(context),
        )

        if (plan.scheduleLocationSync) {
            LocationSyncWorker.schedule(context)
            LocationSyncWorker.enqueueNow(context)
        }

        if (plan.startLocationService) {
            context.startForegroundService(Intent(context, JarvisLocationService::class.java))
        }

        if (plan.showWakeWordReminder) {
            JarvisNotifications.show(
                context,
                JarvisNotifications.DEFAULT,
                "Réactiver l'écoute JARVIS",
                "Android exige une ouverture de l'application après le redémarrage",
            )
        }
    }
}
