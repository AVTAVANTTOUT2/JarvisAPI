package fr.jarvis.companion.data

import android.content.Context
import android.content.SharedPreferences
import android.provider.Settings
import fr.jarvis.companion.BuildConfig

object JarvisSettings {
    const val PREFS = "jarvis_android"
    const val PREF_SERVER = "server_url"
    const val PREF_LOCATION = "background_location"
    const val PREF_WAKE = "wake_word"
    const val PREF_VOICE_CONVERSATION = "voice_conversation_id"

    private const val SECRET_NATIVE_TOKEN = "native_token"
    private const val SECRET_PORCUPINE_KEY = "porcupine_access_key"

    fun preferences(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun server(context: Context): String =
        preferences(context).getString(PREF_SERVER, BuildConfig.DEFAULT_SERVER)?.trim().orEmpty()

    fun setServer(context: Context, url: String) {
        preferences(context).edit().putString(PREF_SERVER, url).apply()
    }

    fun hasServerConfigured(context: Context): Boolean =
        preferences(context).contains(PREF_SERVER)

    fun nativeToken(context: Context): String =
        JarvisSecureStore(context).get(SECRET_NATIVE_TOKEN)

    fun setNativeToken(context: Context, token: String) {
        JarvisSecureStore(context).put(SECRET_NATIVE_TOKEN, token)
    }

    fun clearNativeToken(context: Context) {
        JarvisSecureStore(context).remove(SECRET_NATIVE_TOKEN)
    }

    fun porcupineAccessKey(context: Context): String =
        JarvisSecureStore(context).get(SECRET_PORCUPINE_KEY)

    fun setPorcupineAccessKey(context: Context, accessKey: String) {
        JarvisSecureStore(context).put(SECRET_PORCUPINE_KEY, accessKey)
    }

    fun deviceId(context: Context): String {
        val id = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ANDROID_ID,
        )
        return "android-${id ?: "unknown"}"
    }

    fun isLocationEnabled(context: Context): Boolean =
        preferences(context).getBoolean(PREF_LOCATION, false)

    fun setLocationEnabled(context: Context, enabled: Boolean) {
        preferences(context).edit().putBoolean(PREF_LOCATION, enabled).apply()
    }

    fun isWakeWordEnabled(context: Context): Boolean =
        preferences(context).getBoolean(PREF_WAKE, false)

    fun setWakeWordEnabled(context: Context, enabled: Boolean) {
        preferences(context).edit().putBoolean(PREF_WAKE, enabled).apply()
    }
}
