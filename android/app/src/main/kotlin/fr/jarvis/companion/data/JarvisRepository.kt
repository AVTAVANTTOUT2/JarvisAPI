package fr.jarvis.companion.data

import android.content.Context
import fr.jarvis.companion.network.JarvisApi
import fr.jarvis.companion.network.JarvisApiResult

/** Façade testable au-dessus du client HTTP natif. */
class JarvisRepository(context: Context) {
    private val api = JarvisApi(context.applicationContext)

    fun pingAuthStatus(callback: (JarvisApiResult) -> Unit) = api.pingAuthStatus(callback)

    fun validateNativeToken(callback: (JarvisApiResult) -> Unit) = api.validateNativeToken(callback)

    fun completePairing(code: String, callback: (JarvisApiResult) -> Unit) =
        api.completePairing(code, callback)

    fun registerPushToken(fcmToken: String) = api.registerPushToken(fcmToken)

    fun updateCapabilities(location: Boolean, wakeWord: Boolean) =
        api.updateCapabilities(location, wakeWord)
}
