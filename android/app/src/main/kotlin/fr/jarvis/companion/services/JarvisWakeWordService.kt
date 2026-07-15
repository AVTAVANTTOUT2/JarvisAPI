package fr.jarvis.companion.services

import android.Manifest
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineManager
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import fr.jarvis.companion.voice.VoiceActivity

/** Détection locale du mot « JARVIS » — aucun audio réseau. */
class JarvisWakeWordService : Service() {
    private var manager: PorcupineManager? = null

    override fun onCreate() {
        super.onCreate()
        JarvisNotifications.createChannels(this)
        val notification = JarvisNotifications.foreground(
            this,
            JarvisNotifications.WAKE,
            "JARVIS écoute son nom",
            "Détection locale active",
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val accessKey = JarvisSettings.porcupineAccessKey(this)
        if (accessKey.isEmpty() ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
        ) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (manager == null) {
            try {
                manager = PorcupineManager.Builder()
                    .setAccessKey(accessKey)
                    .setKeyword(Porcupine.BuiltInKeyword.JARVIS)
                    .build(applicationContext) { onWakeWord() }
                manager?.start()
            } catch (e: Exception) {
                JarvisNotifications.show(
                    this,
                    JarvisNotifications.URGENT,
                    "Mot-clé JARVIS indisponible",
                    e.message ?: "Vérifie la clé Picovoice",
                )
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    private fun onWakeWord() {
        val intent = Intent(this, VoiceActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(intent)
        JarvisNotifications.show(
            this,
            JarvisNotifications.URGENT,
            "JARVIS vous écoute, Monsieur",
            "Conversation vocale ouverte — maintenez le micro pour parler",
        )
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        manager?.let {
            try {
                it.stop()
            } catch (_: Exception) {
            }
            it.delete()
        }
        manager = null
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 4102
    }
}
