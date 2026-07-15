package fr.jarvis.companion;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

/** Réception FCM native, y compris lorsque l'interface JARVIS est fermée. */
public final class JarvisMessagingService extends FirebaseMessagingService {
    @Override public void onNewToken(String token) {
        if (!JarvisSettings.nativeToken(this).isEmpty()) {
            new JarvisApi(this).registerPushToken(token);
        }
    }

    @Override public void onMessageReceived(RemoteMessage message) {
        JarvisNotifications.createChannels(this);
        RemoteMessage.Notification notification = message.getNotification();
        Map<String, String> data = message.getData();
        String title = notification != null && notification.getTitle() != null
                ? notification.getTitle() : data.getOrDefault("title", "JARVIS");
        String body = notification != null && notification.getBody() != null
                ? notification.getBody() : data.getOrDefault("body", "Nouvelle information");
        String priority = data.getOrDefault("priority", "medium");
        String channel = ("urgent".equals(priority) || "high".equals(priority))
                ? JarvisNotifications.URGENT : JarvisNotifications.DEFAULT;
        JarvisNotifications.show(this, channel, title, body, false);
    }
}
