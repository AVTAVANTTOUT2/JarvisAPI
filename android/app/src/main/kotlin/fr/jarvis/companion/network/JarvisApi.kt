package fr.jarvis.companion.network

import android.content.Context
import android.os.Build
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.data.JarvisSettings
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import javax.net.ssl.HttpsURLConnection

/**
 * Client HTTPS natif vers le serveur JARVIS.
 * Confiance TLS : CA privée JARVIS pour l'hôte configuré (voir [JarvisTls]), CA système ailleurs.
 */
class JarvisApi(context: Context) {
    private val appContext = context.applicationContext

    fun completePairing(code: String, callback: (JarvisApiResult) -> Unit) {
        val body = JSONObject().apply {
            put("code", code)
            put("device_id", JarvisSettings.deviceId(appContext))
            put("name", "JARVIS sur ${Build.MODEL}")
            put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
            put("app_version", BuildConfig.VERSION_NAME)
        }
        post("/api/mobile/pairing/complete", body, bearer = "", callback)
    }

    fun validateNativeToken(callback: (JarvisApiResult) -> Unit) {
        post("/api/mobile/session", JSONObject(), JarvisSettings.nativeToken(appContext), callback)
    }

    fun pingAuthStatus(callback: (JarvisApiResult) -> Unit) {
        get("/api/auth/status", callback)
    }

    fun registerPushToken(fcmToken: String) {
        val body = JSONObject().put("token", fcmToken)
        post("/api/mobile/push-token", body, JarvisSettings.nativeToken(appContext)) {}
    }

    fun updateCapabilities(location: Boolean, wakeWord: Boolean) {
        val body = JSONObject().apply {
            put("push", BuildConfig.FIREBASE_CONFIGURED)
            put("background_location", location)
            put("wake_word", wakeWord)
        }
        post("/api/mobile/capabilities", body, JarvisSettings.nativeToken(appContext)) {}
    }

    fun postLocation(
        latitude: Double,
        longitude: Double,
        altitude: Double,
        accuracy: Float,
        speed: Float,
        timestamp: Long,
    ) {
        val body = JSONObject().apply {
            put("latitude", latitude)
            put("longitude", longitude)
            put("altitude", altitude)
            put("accuracy", accuracy)
            put("speed", speed)
            put("timestamp", timestamp)
            put("source", "android_background")
        }
        post("/api/location", body, JarvisSettings.nativeToken(appContext)) {}
    }

    private fun get(path: String, callback: (JarvisApiResult) -> Unit) {
        NETWORK.execute {
            callback(request(path, method = "GET", body = null, bearer = ""))
        }
    }

    private fun post(
        path: String,
        body: JSONObject,
        bearer: String,
        callback: (JarvisApiResult) -> Unit,
    ) {
        NETWORK.execute {
            callback(request(path, method = "POST", body = body, bearer = bearer))
        }
    }

    private fun request(
        path: String,
        method: String,
        body: JSONObject?,
        bearer: String,
    ): JarvisApiResult {
        var connection: HttpURLConnection? = null
        return try {
            val url = URL(JarvisSettings.server(appContext) + path)
            connection = (url.openConnection() as HttpURLConnection).apply {
                if (this is HttpsURLConnection &&
                    ServerUrlNormalizer.isJarvisHost(
                        JarvisSettings.server(appContext),
                        url.host,
                    )
                ) {
                    val ctx = JarvisTls.sslContext(appContext)
                    sslSocketFactory = ctx.socketFactory
                }
                requestMethod = method
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "JARVIS-Android/${BuildConfig.VERSION_NAME}")
                if (bearer.isNotEmpty()) {
                    setRequestProperty("Authorization", "Bearer $bearer")
                }
                if (method == "POST" && body != null) {
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    doOutput = true
                    outputStream.use { stream ->
                        stream.write(body.toString().toByteArray(StandardCharsets.UTF_8))
                    }
                }
            }

            val status = connection.responseCode
            val stream = if (status in 200..399) connection.inputStream else connection.errorStream
            val text = readStream(stream)
            val json = if (text.isEmpty()) JSONObject() else JSONObject(text)
            val cookie = connection.getHeaderField("Set-Cookie")
            val error = json.optString("detail", json.optString("error", "HTTP $status"))
            JarvisApiResult(
                ok = status in 200..299,
                status = status,
                json = json,
                cookie = cookie,
                error = error,
            )
        } catch (e: Exception) {
            JarvisApiResult.failure(e.message ?: "erreur réseau")
        } finally {
            if (connection is HttpsURLConnection) {
                connection.disconnect()
            } else {
                connection?.disconnect()
            }
        }
    }

    private fun readStream(stream: InputStream?): String {
        if (stream == null) return ""
        return stream.bufferedReader(StandardCharsets.UTF_8).use(BufferedReader::readText)
    }

    companion object {
        private val NETWORK: ExecutorService = Executors.newFixedThreadPool(2)
        private const val CONNECT_TIMEOUT_MS = 12_000
        private const val READ_TIMEOUT_MS = 20_000
    }
}
