package fr.jarvis.companion;

import android.Manifest;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import ai.picovoice.porcupine.Porcupine;
import ai.picovoice.porcupine.PorcupineManager;

/** Écoute locale du mot « JARVIS » ; aucun audio n'est envoyé au réseau. */
public final class JarvisWakeWordService extends Service {
    private static final int NOTIFICATION_ID = 4102;
    private PorcupineManager manager;

    @Override public void onCreate() {
        super.onCreate();
        JarvisNotifications.createChannels(this);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                    NOTIFICATION_ID,
                    JarvisNotifications.foreground(
                            this,
                            JarvisNotifications.WAKE,
                            "JARVIS écoute son nom",
                            "Détection locale active"
                    ),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            );
        } else {
            startForeground(NOTIFICATION_ID, JarvisNotifications.foreground(
                    this,
                    JarvisNotifications.WAKE,
                    "JARVIS écoute son nom",
                    "Détection locale active"
            ));
        }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String accessKey = JarvisSettings.porcupineAccessKey(this);
        if (accessKey.isEmpty()
                || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (manager == null) {
            try {
                manager = new PorcupineManager.Builder()
                        .setAccessKey(accessKey)
                        .setKeyword(Porcupine.BuiltInKeyword.JARVIS)
                        .build(getApplicationContext(), keywordIndex -> onWakeWord());
                manager.start();
            } catch (Exception e) {
                JarvisNotifications.show(
                        this,
                        JarvisNotifications.URGENT,
                        "Mot-clé JARVIS indisponible",
                        e.getMessage() == null ? "Vérifie la clé Picovoice" : e.getMessage(),
                        false
                );
                stopSelf();
                return START_NOT_STICKY;
            }
        }
        return START_STICKY;
    }

    private void onWakeWord() {
        JarvisNotifications.show(
                this,
                JarvisNotifications.URGENT,
                "JARVIS vous écoute, Monsieur",
                "Touchez pour lancer la conversation vocale",
                true
        );
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        if (manager != null) {
            try { manager.stop(); } catch (Exception ignored) {}
            manager.delete();
            manager = null;
        }
        super.onDestroy();
    }
}
