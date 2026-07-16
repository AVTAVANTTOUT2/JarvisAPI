package fr.jarvis.companion.core.network

import android.content.Context
import com.google.gson.Gson
import com.google.gson.JsonObject
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.JarvisTls
import fr.jarvis.companion.network.ServerUrlNormalizer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import kotlin.math.min
import kotlin.random.Random

sealed class WsConnectionState {
    data object Disconnected : WsConnectionState()
    data object Connecting : WsConnectionState()
    data object Connected : WsConnectionState()
    data object Reconnecting : WsConnectionState()
    data object AuthenticationFailed : WsConnectionState()
    data object ServerUnavailable : WsConnectionState()
}

data class WsIncomingMessage(
    val type: String,
    val content: String? = null,
    val conversationId: Long? = null,
    val title: String? = null,
    val message: String? = null,
    val action: JsonObject? = null,
    val actionType: String? = null,
    val raw: JsonObject,
)

interface ChatWebSocketListener {
    fun onWsMessage(message: WsIncomingMessage)
    fun onWsConnectionState(state: WsConnectionState)
}

class JarvisChatWebSocket(
    context: Context,
    private val httpClientOverride: OkHttpClient? = null,
) {
    private val appContext = context.applicationContext
    private val gson = Gson()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val connectMutex = Mutex()

    private val _connectionState = MutableStateFlow<WsConnectionState>(WsConnectionState.Disconnected)
    val connectionState: StateFlow<WsConnectionState> = _connectionState.asStateFlow()

    private var webSocket: WebSocket? = null
    private var listener: ChatWebSocketListener? = null
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private var intentionalClose = false
    private var activeConversationServerId: Long? = null

    private val client: OkHttpClient by lazy {
        httpClientOverride ?: OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(30, TimeUnit.SECONDS)
            .sslSocketFactory(
                JarvisTls.sslContext(appContext).socketFactory,
                JarvisTls.serverTrustManager(appContext),
            )
            .build()
    }

    fun setListener(listener: ChatWebSocketListener?) {
        this.listener = listener
    }

    fun connect(conversationServerId: Long? = null) {
        activeConversationServerId = conversationServerId
        intentionalClose = false
        reconnectAttempt = 0
        scope.launch { connectInternal(isReconnect = false) }
    }

    fun disconnect() {
        intentionalClose = true
        reconnectJob?.cancel()
        webSocket?.close(1000, "client disconnect")
        webSocket = null
        _connectionState.value = WsConnectionState.Disconnected
        listener?.onWsConnectionState(WsConnectionState.Disconnected)
    }

    fun sendText(content: String, stream: Boolean = true): Boolean {
        return sendJson(
            mapOf(
                "type" to "text",
                "content" to content,
                "stream" to stream,
                "tts" to false,
            ),
        )
    }

    fun sendNewConversation(): Boolean = sendJson(mapOf("type" to "new_conversation"))

    fun switchConversation(conversationServerId: Long): Boolean {
        activeConversationServerId = conversationServerId
        return sendJson(
            mapOf(
                "type" to "switch_conversation",
                "conversation_id" to conversationServerId,
            ),
        )
    }

    fun sendActionConfirm(action: JsonObject, confirmed: Boolean = true): Boolean {
        return sendJson(
            mapOf(
                "type" to "action_confirm",
                "action" to action,
                "confirmed" to confirmed,
            ),
        )
    }

    private fun sendJson(payload: Map<String, Any?>): Boolean {
        val ws = webSocket ?: return false
        val json = gson.toJson(payload)
        return ws.send(json)
    }

    private suspend fun connectInternal(isReconnect: Boolean) {
        connectMutex.withLock {
            if (intentionalClose) return
            val token = JarvisSettings.nativeToken(appContext)
            if (token.isBlank()) {
                _connectionState.value = WsConnectionState.AuthenticationFailed
                listener?.onWsConnectionState(WsConnectionState.AuthenticationFailed)
                return
            }
            val base = ServerUrlNormalizer.normalize(JarvisSettings.server(appContext))
            if (base == null) {
                _connectionState.value = WsConnectionState.ServerUnavailable
                listener?.onWsConnectionState(WsConnectionState.ServerUnavailable)
                return
            }
            val wsUrl = base.replace("https://", "wss://").trimEnd('/') + "/ws"
            _connectionState.value = if (isReconnect) WsConnectionState.Reconnecting else WsConnectionState.Connecting
            listener?.onWsConnectionState(_connectionState.value)

            val request = Request.Builder()
                .url(wsUrl)
                .header("Authorization", "Bearer $token")
                .header("User-Agent", "JARVIS-Android/${BuildConfig.VERSION_NAME}")
                .build()

            webSocket?.cancel()
            webSocket = client.newWebSocket(request, socketListener)
        }
    }

    private val socketListener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            reconnectAttempt = 0
            _connectionState.value = WsConnectionState.Connected
            listener?.onWsConnectionState(WsConnectionState.Connected)
            activeConversationServerId?.let { serverId ->
                switchConversation(serverId)
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val parsed = runCatching { gson.fromJson(text, JsonObject::class.java) }.getOrNull() ?: return
            val type = parsed.get("type")?.asString ?: return
            val incoming = WsIncomingMessage(
                type = type,
                content = parsed.get("content")?.asString,
                conversationId = parsed.get("conversation_id")?.takeIf { it.isJsonPrimitive }?.asLong,
                title = parsed.get("title")?.asString,
                message = parsed.get("message")?.asString ?: parsed.get("error")?.asString,
                action = parsed.get("action")?.takeIf { it.isJsonObject }?.asJsonObject,
                actionType = parsed.get("action_type")?.asString,
                raw = parsed,
            )
            listener?.onWsMessage(incoming)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            handleDisconnect(code)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val code = response?.code
            if (code == 401 || code == 4401) {
                intentionalClose = true
                _connectionState.value = WsConnectionState.AuthenticationFailed
                listener?.onWsConnectionState(WsConnectionState.AuthenticationFailed)
                return
            }
            handleDisconnect(code ?: -1)
        }
    }

    private fun handleDisconnect(closeCode: Int) {
        webSocket = null
        if (intentionalClose || closeCode == 401 || closeCode == 4401) {
            _connectionState.value = WsConnectionState.Disconnected
            listener?.onWsConnectionState(WsConnectionState.Disconnected)
            return
        }
        _connectionState.value = WsConnectionState.ServerUnavailable
        listener?.onWsConnectionState(WsConnectionState.ServerUnavailable)
        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        if (intentionalClose) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val baseDelay = min(30_000L, 1_000L * (1 shl min(reconnectAttempt, 5)))
            val jitter = Random.nextLong(0, 500)
            delay(baseDelay + jitter)
            reconnectAttempt++
            connectInternal(isReconnect = true)
        }
    }
}
