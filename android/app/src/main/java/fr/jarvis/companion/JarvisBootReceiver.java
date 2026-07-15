package fr.jarvis.companion;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

/** Restaure la présence GPS ; le micro exige une action visible après un redémarrage. */
public final class JarvisBootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        JarvisNotifications.createChannels(context);
        boolean locationEnabled = JarvisSettings.preferences(context)
                .getBoolean(JarvisSettings.PREF_LOCATION, false);
        if (locationEnabled
                && context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED
                && context.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                == PackageManager.PERMISSION_GRANTED) {
            context.startForegroundService(new Intent(context, JarvisLocationService.class));
        }

        boolean wakeEnabled = JarvisSettings.preferences(context)
                .getBoolean(JarvisSettings.PREF_WAKE, false);
        if (wakeEnabled) {
            JarvisNotifications.show(
                    context,
                    JarvisNotifications.DEFAULT,
                    "Réactiver l'écoute JARVIS",
                    "Android exige une ouverture de l'application après le redémarrage",
                    false
            );
        }
    }
}
