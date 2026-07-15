package fr.jarvis.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

final class JarvisNotifications {
    static final String DEFAULT = "jarvis_default";
    static final String URGENT = "jarvis_urgent";
    static final String PRESENCE = "jarvis_presence";
    static final String WAKE = "jarvis_wake_word";

    private JarvisNotifications() {}

    static void createChannels(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        manager.createNotificationChannel(new NotificationChannel(
                DEFAULT, "JARVIS", NotificationManager.IMPORTANCE_DEFAULT));
        manager.createNotificationChannel(new NotificationChannel(
                URGENT, "JARVIS prioritaire", NotificationManager.IMPORTANCE_HIGH));
        manager.createNotificationChannel(new NotificationChannel(
                PRESENCE, "Présence GPS JARVIS", NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(
                WAKE, "Écoute du mot JARVIS", NotificationManager.IMPORTANCE_LOW));
    }

    static Notification foreground(Context context, String channel, String title, String body) {
        return builder(context, channel, title, body, false).setOngoing(true).build();
    }

    static void show(Context context, String channel, String title, String body, boolean voice) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.notify((int) (System.currentTimeMillis() & 0x7fffffff),
                    builder(context, channel, title, body, voice).build());
        }
    }

    private static Notification.Builder builder(
            Context context, String channel, String title, String body, boolean voice) {
        Intent intent = new Intent(context, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (voice) intent.putExtra("open_voice", true);
        PendingIntent pending = PendingIntent.getActivity(
                context,
                voice ? 2 : 1,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, channel) : new Notification.Builder(context);
        return builder.setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setContentIntent(pending)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_MESSAGE);
    }
}
