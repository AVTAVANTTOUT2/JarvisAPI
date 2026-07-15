package fr.jarvis.companion.network

import com.google.gson.JsonObject
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST

interface JarvisApiService {
    @GET("api/auth/status")
    suspend fun authStatus(): Response<JsonObject>

    @POST("api/mobile/pairing/complete")
    suspend fun completePairing(@Body body: PairingCompleteRequest): Response<JsonObject>

    @POST("api/mobile/session")
    suspend fun validateNativeToken(
        @Header("Authorization") authorization: String,
        @Body body: EmptyBody = EmptyBody(),
    ): Response<JsonObject>

    @POST("api/mobile/push-token")
    suspend fun registerPushToken(
        @Header("Authorization") authorization: String,
        @Body body: PushTokenRequest,
    ): Response<JsonObject>

    @POST("api/mobile/capabilities")
    suspend fun updateCapabilities(
        @Header("Authorization") authorization: String,
        @Body body: CapabilitiesRequest,
    ): Response<JsonObject>

    @POST("api/location")
    suspend fun postLocation(
        @Header("Authorization") authorization: String,
        @Body body: LocationRequest,
    ): Response<JsonObject>
}

data class EmptyBody(val noop: Boolean = true)

data class PairingCompleteRequest(
    val code: String,
    val device_id: String,
    val name: String,
    val model: String,
    val app_version: String,
)

data class PushTokenRequest(val token: String)

data class CapabilitiesRequest(
    val push: Boolean,
    val background_location: Boolean,
    val wake_word: Boolean,
)

data class LocationRequest(
    val latitude: Double,
    val longitude: Double,
    val altitude: Double,
    val accuracy: Float,
    val speed: Float,
    val timestamp: Long,
    val source: String = "android_background",
)
