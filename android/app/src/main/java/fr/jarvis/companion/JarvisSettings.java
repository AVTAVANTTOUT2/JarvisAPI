package fr.jarvis.companion;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

final class JarvisSettings {
    static final String PREFS = "jarvis_android";
    static final String PREF_SERVER = "server_url";
    static final String PREF_LOCATION = "background_location";
    static final String PREF_WAKE = "wake_word";
    static final String DEFAULT_SERVER = "https://100.123.50.38:8081";
    private static final String SECRET_NATIVE_TOKEN = "native_token";
    private static final String SECRET_PORCUPINE_KEY = "porcupine_access_key";

    private JarvisSettings() {}

    static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String server(Context context) {
        return preferences(context).getString(PREF_SERVER, DEFAULT_SERVER);
    }

    static String nativeToken(Context context) {
        return new JarvisSecureStore(context).get(SECRET_NATIVE_TOKEN);
    }

    static void setNativeToken(Context context, String token) {
        new JarvisSecureStore(context).put(SECRET_NATIVE_TOKEN, token);
    }

    static void clearNativeToken(Context context) {
        new JarvisSecureStore(context).remove(SECRET_NATIVE_TOKEN);
    }

    static String porcupineAccessKey(Context context) {
        return new JarvisSecureStore(context).get(SECRET_PORCUPINE_KEY);
    }

    static void setPorcupineAccessKey(Context context, String accessKey) {
        new JarvisSecureStore(context).put(SECRET_PORCUPINE_KEY, accessKey);
    }

    static String deviceId(Context context) {
        String id = Settings.Secure.getString(
                context.getContentResolver(), Settings.Secure.ANDROID_ID
        );
        return "android-" + (id == null ? "unknown" : id);
    }
}
