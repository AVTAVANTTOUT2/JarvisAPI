package fr.jarvis.companion.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import fr.jarvis.companion.R
import fr.jarvis.companion.ui.MainActivity

object JarvisNotifications {
    const val DEFAULT = "jarvis_default"
    const val URGENT = "jarvis_urgent"
    const val PRESENCE = "jarvis_presence"
    const val WAKE = "jarvis_wake_word"

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(DEFAULT, "JARVIS", NotificationManager.IMPORTANCE_DEFAULT),
        )
        manager.createNotificationChannel(
            NotificationChannel(URGENT, "JARVIS prioritaire", NotificationManager.IMPORTANCE_HIGH),
        )
        manager.createNotificationChannel(
            NotificationChannel(PRESENCE, "Présence GPS JARVIS", NotificationManager.IMPORTANCE_LOW),
        )
        manager.createNotificationChannel(
            NotificationChannel(WAKE, "Écoute du mot JARVIS", NotificationManager.IMPORTANCE_LOW),
        )
    }

    fun foreground(context: Context, channel: String, title: String, body: String): Notification =
        builder(context, channel, title, body, openApp = true).setOngoing(true).build()

    fun show(context: Context, channel: String, title: String, body: String) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.notify(
            (System.currentTimeMillis() and 0x7fffffffL).toInt(),
            builder(context, channel, title, body, openApp = true).build(),
        )
    }

    private fun builder(
        context: Context,
        channel: String,
        title: String,
        body: String,
        openApp: Boolean,
    ): Notification.Builder {
        val intent = Intent(context, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        val pending = PendingIntent.getActivity(
            context,
            if (openApp) 1 else 2,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(context, channel)
        } else {
            Notification.Builder(context)
        }
        return builder
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
    }
}
