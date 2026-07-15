package fr.jarvis.companion.voice

import android.content.Context
import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.JarvisTls
import fr.jarvis.companion.network.ServerUrlNormalizer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.TimeUnit

data class VoiceTurnResponse(
    @SerializedName("conversation_id") val conversationId: Long,
    @SerializedName("transcript") val transcript: String,
    @SerializedName("response_text") val responseText: String,
    @SerializedName("audio_base64") val audioBase64: String?,
    @SerializedName("audio_mime_type") val audioMimeType: String?,
    @SerializedName("stt_engine") val sttEngine: String?,
    @SerializedName("stt_model") val sttModel: String?,
    @SerializedName("tts_engine") val ttsEngine: String?,
    @SerializedName("source") val source: String?,
    @SerializedName("device_id") val deviceId: String?,
    @SerializedName("tts_error") val ttsError: String?,
)

sealed class VoiceApiResult {
    data class Success(val body: VoiceTurnResponse) : VoiceApiResult()
    data class Failure(val message: String, val httpCode: Int? = null) : VoiceApiResult()
}

/** Envoi multipart HTTPS vers POST /api/mobile/voice/turn. */
class VoiceRepository(
    context: Context,
    private val httpClientOverride: OkHttpClient? = null,
) {
    private val appContext = context.applicationContext
    private val gson = Gson()

    private val client: OkHttpClient by lazy {
        httpClientOverride ?: OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(VOICE_READ_TIMEOUT_SEC, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .sslSocketFactory(
                JarvisTls.sslContext(appContext).socketFactory,
                JarvisTls.serverTrustManager(appContext),
            )
            .build()
    }

    fun serverUrl(): String = JarvisSettings.server(appContext)

    fun isHttpsConfigured(): Boolean =
        ServerUrlNormalizer.normalize(serverUrl()) != null

    fun hasToken(): Boolean = JarvisSettings.nativeToken(appContext).isNotBlank()

    suspend fun sendVoiceTurn(
        audioFile: File,
        conversationId: Long?,
    ): VoiceApiResult {
        val base = ServerUrlNormalizer.normalize(serverUrl())
            ?: return VoiceApiResult.Failure("Adresse HTTPS du serveur invalide")
        val token = JarvisSettings.nativeToken(appContext)
        if (token.isBlank()) {
            return VoiceApiResult.Failure("Téléphone non appairé")
        }
        val mime = "audio/mp4".toMediaType()
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "audio",
                audioFile.name,
                audioFile.asRequestBody(mime),
            )
        if (conversationId != null) {
            multipart.addFormDataPart("conversation_id", conversationId.toString())
        }
        val request = Request.Builder()
            .url("${base.trimEnd('/')}/api/mobile/voice/turn")
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/json")
            .header("User-Agent", "JARVIS-Android/${BuildConfig.VERSION_NAME}")
            .post(multipart.build())
            .build()
        return runCatching {
            client.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    val message = runCatching {
                        gson.fromJson(raw, Map::class.java)["detail"]?.toString()
                    }.getOrNull() ?: "Erreur serveur (${response.code})"
                    return VoiceApiResult.Failure(message, response.code)
                }
                VoiceApiResult.Success(gson.fromJson(raw, VoiceTurnResponse::class.java))
            }
        }.getOrElse {
            VoiceApiResult.Failure(it.message ?: "Réseau indisponible")
        }
    }

    companion object {
        private const val VOICE_READ_TIMEOUT_SEC = 180L
    }
}
