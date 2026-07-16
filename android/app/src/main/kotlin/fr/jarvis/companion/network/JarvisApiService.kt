package fr.jarvis.companion.network

import com.google.gson.JsonObject
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path

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

    @POST("api/location/batch")
    suspend fun postLocationBatch(
        @Header("Authorization") authorization: String,
        @Body body: LocationBatchRequest,
    ): Response<LocationBatchResponse>

    @GET("api/mobile/location/diagnostics")
    suspend fun getLocationDiagnostics(
        @Header("Authorization") authorization: String,
    ): Response<LocationDiagnosticsResponse>

    @GET("api/briefing")
    suspend fun getBriefing(
        @Header("Authorization") authorization: String,
        @retrofit2.http.Query("kind") kind: String,
    ): Response<JsonObject>

    @GET("api/tasks")
    suspend fun getTasks(
        @Header("Authorization") authorization: String,
        @retrofit2.http.Query("status") status: String = "all",
    ): Response<JsonObject>

    @GET("api/calendar")
    suspend fun getCalendar(
        @Header("Authorization") authorization: String,
        @retrofit2.http.Query("start") start: String,
        @retrofit2.http.Query("end") end: String,
    ): Response<JsonObject>

    @GET("api/notifications")
    suspend fun getNotifications(
        @Header("Authorization") authorization: String,
    ): Response<JsonObject>

    @GET("api/conversations")
    suspend fun getConversations(
        @Header("Authorization") authorization: String,
        @retrofit2.http.Query("archived") archived: Boolean = false,
        @retrofit2.http.Query("limit") limit: Int = 50,
    ): Response<JsonObject>

    @GET("api/conversations/{id}")
    suspend fun getConversationDetail(
        @Header("Authorization") authorization: String,
        @Path("id") id: Long,
    ): Response<JsonObject>

    @PATCH("api/conversations/{id}")
    suspend fun patchConversation(
        @Header("Authorization") authorization: String,
        @Path("id") id: Long,
        @Body body: ConversationPatchRequest,
    ): Response<JsonObject>

    @DELETE("api/conversations/{id}")
    suspend fun deleteConversation(
        @Header("Authorization") authorization: String,
        @Path("id") id: Long,
    ): Response<JsonObject>

    @POST("api/conversations/{id}/pin")
    suspend fun pinConversation(
        @Header("Authorization") authorization: String,
        @Path("id") id: Long,
    ): Response<JsonObject>

    @POST("api/conversations/{id}/archive")
    suspend fun archiveConversation(
        @Header("Authorization") authorization: String,
        @Path("id") id: Long,
    ): Response<JsonObject>

    @POST("api/mobile/conversations")
    suspend fun createMobileConversation(
        @Header("Authorization") authorization: String,
        @Body body: MobileCreateConversationRequest = MobileCreateConversationRequest(),
    ): Response<JsonObject>

    @POST("api/mobile/chat")
    suspend fun sendMobileChat(
        @Header("Authorization") authorization: String,
        @Body body: MobileChatRequest,
    ): Response<JsonObject>

    @POST("api/mobile/chat/confirm")
    suspend fun confirmMobileChat(
        @Header("Authorization") authorization: String,
        @Body body: MobileChatConfirmRequest,
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

data class LocationBatchRequest(
    val points: List<LocationBatchPoint>,
)

data class LocationBatchPoint(
    val client_point_id: String,
    val latitude: Double,
    val longitude: Double,
    val altitude: Double? = null,
    val accuracy: Float,
    val speed: Float? = null,
    val bearing: Float? = null,
    val provider: String? = null,
    val captured_at: Long,
    val source: String = "android_background",
)

data class LocationBatchResponse(
    val accepted: List<String> = emptyList(),
    val duplicates: List<String> = emptyList(),
    val rejected: List<LocationBatchRejected> = emptyList(),
)

data class LocationBatchRejected(
    val client_point_id: String,
    val reason: String,
)

data class LocationBatchResult(
    val ok: Boolean,
    val status: Int,
    val accepted: List<String> = emptyList(),
    val duplicates: List<String> = emptyList(),
    val rejected: List<LocationBatchRejected> = emptyList(),
    val unauthorized: Boolean = false,
    val error: String = "",
)

data class LocationDiagnosticsResponse(
    val device_id: String = "",
    val points_received_24h: Int = 0,
    val last_point_received_at: String? = null,
)

data class LocationDiagnosticsResult(
    val ok: Boolean,
    val status: Int,
    val body: LocationDiagnosticsResponse? = null,
    val unauthorized: Boolean = false,
    val error: String = "",
)

data class ConversationPatchRequest(
    val title: String? = null,
    val pinned: Boolean? = null,
    val archived: Boolean? = null,
)

data class MobileCreateConversationRequest(
    val title: String? = null,
)

data class MobileChatRequest(
    val content: String,
    val conversation_id: Long? = null,
    val client_message_id: String? = null,
)

data class MobileChatConfirmRequest(
    val conversation_id: Long,
    val confirmed: Boolean,
)
