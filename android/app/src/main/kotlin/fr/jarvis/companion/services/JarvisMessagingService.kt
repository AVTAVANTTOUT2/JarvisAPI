package fr.jarvis.companion.services

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.JarvisApi
import fr.jarvis.companion.notifications.JarvisNotifications

/** Réception FCM native lorsque l'application est fermée. */
class JarvisMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        if (JarvisSettings.nativeToken(this).isNotEmpty()) {
            JarvisApi(this).registerPushToken(token)
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        JarvisNotifications.createChannels(this)
        val notification = message.notification
        val data = message.data
        val title = notification?.title ?: data["title"] ?: "JARVIS"
        val body = notification?.body ?: data["body"] ?: "Nouvelle information"
        val priority = data["priority"] ?: "medium"
        val channel = if (priority == "urgent" || priority == "high") {
            JarvisNotifications.URGENT
        } else {
            JarvisNotifications.DEFAULT
        }
        JarvisNotifications.show(this, channel, title, body)
    }
}
