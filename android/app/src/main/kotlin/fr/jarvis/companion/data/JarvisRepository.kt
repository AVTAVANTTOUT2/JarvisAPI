package fr.jarvis.companion.data

import android.content.Context
import android.os.Build
import com.google.gson.JsonObject
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.network.CapabilitiesRequest
import fr.jarvis.companion.network.JarvisApiResult
import fr.jarvis.companion.network.JarvisHttpClient
import fr.jarvis.companion.network.LocationRequest
import fr.jarvis.companion.network.PairingCompleteRequest
import fr.jarvis.companion.network.PushTokenRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import retrofit2.Response

/** Accès réseau suspendu — ViewModel et services passent par cette couche. */
class JarvisRepository(context: Context) {
    private val appContext = context.applicationContext
    private val http = JarvisHttpClient(appContext)

    private fun api() = http.service(JarvisSettings.server(appContext))

    private fun bearer(): String = "Bearer ${JarvisSettings.nativeToken(appContext)}"

    suspend fun pingAuthStatus(): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().authStatus()) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun validateNativeToken(): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().validateNativeToken(bearer())) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun completePairing(code: String): JarvisApiResult = withContext(Dispatchers.IO) {
        val body = PairingCompleteRequest(
            code = code,
            device_id = JarvisSettings.deviceId(appContext),
            name = "JARVIS sur ${Build.MODEL}",
            model = "${Build.MANUFACTURER} ${Build.MODEL}",
            app_version = BuildConfig.VERSION_NAME,
        )
        runCatching { toResult(api().completePairing(body)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun registerPushToken(fcmToken: String): JarvisApiResult = withContext(Dispatchers.IO) {
        if (fcmToken.isBlank()) return@withContext JarvisApiResult.failure("token FCM vide")
        runCatching {
            toResult(api().registerPushToken(bearer(), PushTokenRequest(fcmToken)))
        }.getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun updateCapabilities(location: Boolean, wakeWord: Boolean): JarvisApiResult =
        withContext(Dispatchers.IO) {
            val body = CapabilitiesRequest(
                push = BuildConfig.FIREBASE_CONFIGURED,
                background_location = location,
                wake_word = wakeWord,
            )
            runCatching { toResult(api().updateCapabilities(bearer(), body)) }
                .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
        }

    suspend fun postLocation(
        latitude: Double,
        longitude: Double,
        altitude: Double,
        accuracy: Float,
        speed: Float,
        timestamp: Long,
    ): JarvisApiResult = withContext(Dispatchers.IO) {
        val body = LocationRequest(
            latitude = latitude,
            longitude = longitude,
            altitude = altitude,
            accuracy = accuracy,
            speed = speed,
            timestamp = timestamp,
        )
        runCatching { toResult(api().postLocation(bearer(), body)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun getBriefing(kind: String): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getBriefing(bearer(), kind)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun getTasks(status: String = "all"): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getTasks(bearer(), status)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun getCalendar(start: String, end: String): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getCalendar(bearer(), start, end)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun getNotifications(): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getNotifications(bearer())) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun getConversations(limit: Int = 20): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getConversations(bearer(), limit = limit)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    fun invalidateHttpCache() = http.invalidateCache()

    private fun toResult(response: Response<JsonObject>): JarvisApiResult {
        val status = response.code()
        val jsonObject = response.body()
        val errorBody = response.errorBody()?.string().orEmpty()
        val json = when {
            jsonObject != null -> JSONObject(jsonObject.toString())
            errorBody.isNotEmpty() -> runCatching { JSONObject(errorBody) }.getOrElse { JSONObject() }
            else -> JSONObject()
        }
        val error = json.optString("detail", json.optString("error", "HTTP $status"))
        return JarvisApiResult(
            ok = response.isSuccessful,
            status = status,
            json = json,
            cookie = null,
            error = error,
        )
    }
}
