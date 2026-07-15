package fr.jarvis.companion.receivers

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import fr.jarvis.companion.services.JarvisLocationService

/** Restaure la présence GPS après redémarrage ; le micro exige une action visible. */
class JarvisBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        JarvisNotifications.createChannels(context)
        val locationEnabled = JarvisSettings.isLocationEnabled(context)
        if (locationEnabled &&
            context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED &&
            context.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        ) {
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
