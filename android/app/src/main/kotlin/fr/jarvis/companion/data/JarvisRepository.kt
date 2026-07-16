package fr.jarvis.companion.data

import android.content.Context
import android.os.Build
import com.google.gson.JsonObject
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.network.CapabilitiesRequest
import fr.jarvis.companion.network.ConversationPatchRequest
import fr.jarvis.companion.network.JarvisApiResult
import fr.jarvis.companion.network.JarvisHttpClient
import fr.jarvis.companion.network.LocationBatchRequest
import fr.jarvis.companion.network.LocationBatchResult
import fr.jarvis.companion.network.LocationRequest
import fr.jarvis.companion.network.MobileChatConfirmRequest
import fr.jarvis.companion.network.MobileChatRequest
import fr.jarvis.companion.network.MobileCreateConversationRequest
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

    suspend fun postLocationBatch(request: LocationBatchRequest): LocationBatchResult =
        withContext(Dispatchers.IO) {
            if (JarvisSettings.nativeToken(appContext).isBlank()) {
                return@withContext LocationBatchResult(
                    ok = false,
                    status = 0,
                    unauthorized = true,
                    error = "Jeton absent",
                )
            }
            runCatching {
                val response = api().postLocationBatch(bearer(), request)
                val status = response.code()
                if (status == 401) {
                    return@runCatching LocationBatchResult(
                        ok = false,
                        status = status,
                        unauthorized = true,
                        error = response.errorBody()?.string()?.ifBlank { "Non autorisé" } ?: "Non autorisé",
                    )
                }
                val body = response.body()
                if (response.isSuccessful && body != null) {
                    LocationBatchResult(
                        ok = true,
                        status = status,
                        accepted = body.accepted,
                        duplicates = body.duplicates,
                        rejected = body.rejected,
                    )
                } else {
                    LocationBatchResult(
                        ok = false,
                        status = status,
                        error = response.errorBody()?.string()?.ifBlank { "HTTP $status" } ?: "HTTP $status",
                    )
                }
            }.getOrElse {
                LocationBatchResult(
                    ok = false,
                    status = 0,
                    error = it.message ?: "erreur réseau",
                )
            }
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

    suspend fun getConversations(limit: Int = 50, archived: Boolean = false): JarvisApiResult =
        withContext(Dispatchers.IO) {
            runCatching { toResult(api().getConversations(bearer(), archived = archived, limit = limit)) }
                .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
        }

    suspend fun getConversationDetail(id: Long): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().getConversationDetail(bearer(), id)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun patchConversation(id: Long, title: String? = null, pinned: Boolean? = null, archived: Boolean? = null): JarvisApiResult =
        withContext(Dispatchers.IO) {
            val body = ConversationPatchRequest(title = title, pinned = pinned, archived = archived)
            runCatching { toResult(api().patchConversation(bearer(), id, body)) }
                .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
        }

    suspend fun deleteConversation(id: Long): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().deleteConversation(bearer(), id)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun pinConversation(id: Long): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().pinConversation(bearer(), id)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun archiveConversation(id: Long): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching { toResult(api().archiveConversation(bearer(), id)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun createMobileConversation(title: String? = null): JarvisApiResult = withContext(Dispatchers.IO) {
        runCatching {
            toResult(api().createMobileConversation(bearer(), MobileCreateConversationRequest(title = title)))
        }.getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun sendMobileChat(
        content: String,
        conversationId: Long? = null,
        clientMessageId: String? = null,
    ): JarvisApiResult = withContext(Dispatchers.IO) {
        val body = MobileChatRequest(
            content = content,
            conversation_id = conversationId,
            client_message_id = clientMessageId,
        )
        runCatching { toResult(api().sendMobileChat(bearer(), body)) }
            .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
    }

    suspend fun confirmMobileChat(conversationId: Long, confirmed: Boolean): JarvisApiResult =
        withContext(Dispatchers.IO) {
            val body = MobileChatConfirmRequest(conversation_id = conversationId, confirmed = confirmed)
            runCatching { toResult(api().confirmMobileChat(bearer(), body)) }
                .getOrElse { JarvisApiResult.failure(it.message ?: "erreur réseau") }
        }

    fun bearerToken(): String = JarvisSettings.nativeToken(appContext)

    fun serverBaseUrl(): String = JarvisSettings.server(appContext)

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
