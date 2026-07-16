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
        JarvisNotifications.createChannels(context)

        if (JarvisSettings.nativeToken(context).isNotEmpty()) {
            LocationSyncWorker.schedule(context)
            LocationSyncWorker.enqueueNow(context)
        }

        val locationEnabled = JarvisSettings.isLocationEnabled(context)
        val hasFine = context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        if (locationEnabled && hasFine) {
            context.startForegroundService(Intent(context, JarvisLocationService::class.java))
        }

        if (JarvisSettings.isWakeWordEnabled(context)) {
            JarvisNotifications.show(
                context,
                JarvisNotifications.DEFAULT,
                "Réactiver l'écoute JARVIS",
                "Android exige une ouverture de l'application après le redémarrage",
            )
        }
    }
}
