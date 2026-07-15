package fr.jarvis.companion.network

import org.json.JSONObject

data class JarvisApiResult(
    val ok: Boolean,
    val status: Int,
    val json: JSONObject,
    val cookie: String?,
    val error: String,
) {
    companion object {
        fun failure(message: String, status: Int = 0): JarvisApiResult =
            JarvisApiResult(
                ok = false,
                status = status,
                json = JSONObject(),
                cookie = null,
                error = message,
            )
    }
}
