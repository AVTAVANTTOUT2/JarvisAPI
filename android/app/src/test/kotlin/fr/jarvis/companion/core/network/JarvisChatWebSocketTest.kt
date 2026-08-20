package fr.jarvis.companion.core.network

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.gson.Gson
import fr.jarvis.companion.data.FakeSecretKeyProvider
import fr.jarvis.companion.data.JarvisSecureStore
import fr.jarvis.companion.data.JarvisSettings
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.net.InetAddress
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class JarvisChatWebSocketTest {
    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var httpClient: OkHttpClient

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        JarvisSecureStore.defaultKeyProvider = FakeSecretKeyProvider()

        val certificate = HeldCertificate.Builder()
            .addSubjectAlternativeName("localhost")
            .addSubjectAlternativeName("127.0.0.1")
            .build()
        val serverCertificates = HandshakeCertificates.Builder()
            .heldCertificate(certificate)
            .build()
        val clientCertificates = HandshakeCertificates.Builder()
            .addTrustedCertificate(certificate.certificate)
            .build()

        server = MockWebServer()
        server.useHttps(serverCertificates.sslSocketFactory(), false)
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        httpClient = OkHttpClient.Builder()
            .sslSocketFactory(
                clientCertificates.sslSocketFactory(),
                clientCertificates.trustManager,
            )
            .build()
        JarvisSettings.setServer(context, "https://127.0.0.1:${server.port}")
        JarvisSettings.setNativeToken(context, "websocket-token")
    }

    @After
    fun tearDown() {
        JarvisSettings.clearNativeToken(context)
        server.shutdown()
    }

    @Test
    fun opensAuthenticatedWssAndExchangesTypedMessages() {
        val serverSocket = AtomicReference<WebSocket>()
        val outbound = CopyOnWriteArrayList<String>()
        val outboundLatch = CountDownLatch(2)
        val closed = CountDownLatch(1)
        server.enqueue(
            MockResponse().withWebSocketUpgrade(
                object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        serverSocket.set(webSocket)
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        outbound += text
                        outboundLatch.countDown()
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(code, reason)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        closed.countDown()
                    }
                },
            ),
        )

        val connected = CountDownLatch(1)
        val incoming = CountDownLatch(1)
        val agenticIncoming = CountDownLatch(1)
        val received = AtomicReference<WsIncomingMessage>()
        val socket = JarvisChatWebSocket(context, httpClient)
        socket.setListener(
            object : ChatWebSocketListener {
                override fun onWsMessage(message: WsIncomingMessage) {
                    received.set(message)
                    incoming.countDown()
                    if (message.agenticEvent != null) {
                        agenticIncoming.countDown()
                    }
                }

                override fun onWsConnectionState(state: WsConnectionState) {
                    if (state == WsConnectionState.Connected) connected.countDown()
                }
            },
        )

        socket.connect(conversationServerId = 42)
        assertTrue(connected.await(3, TimeUnit.SECONDS))
        assertTrue(socket.sendText("Bonjour"))
        assertTrue(outboundLatch.await(3, TimeUnit.SECONDS))

        val request = server.takeRequest(3, TimeUnit.SECONDS)
        assertNotNull(request)
        assertNotNull(request!!.handshake)
        assertEquals("/ws", request.path)
        assertEquals("Bearer websocket-token", request.getHeader("Authorization"))
        assertTrue(outbound.any { "\"type\":\"switch_conversation\"" in it && "42" in it })
        assertTrue(outbound.any { "\"type\":\"text\"" in it && "Bonjour" in it })

        serverSocket.get().send(
            """{"type":"response","content":"Bonsoir","conversation_id":42}""",
        )
        assertTrue(incoming.await(3, TimeUnit.SECONDS))
        assertEquals("response", received.get().type)
        assertEquals("Bonsoir", received.get().content)
        assertEquals(42L, received.get().conversationId)

        val agenticEvent = runBlocking {
            val nextEvent = async(start = CoroutineStart.UNDISPATCHED) {
                socket.agenticEvents.first()
            }
            serverSocket.get().send(
                """{"type":"agent.approval.requested","data":{"run_id":"run-android","approval_id":"apr_opaque-42","phase":"awaiting_approval","prompt":"sensitive prompt","arguments":{"token":"secret"}}}""",
            )
            withTimeout(3_000) { nextEvent.await() }
        }
        assertTrue(agenticIncoming.await(3, TimeUnit.SECONDS))
        assertEquals("run-android", agenticEvent.run_id)
        assertEquals("apr_opaque-42", agenticEvent.approval_id)
        assertEquals("awaiting_approval", agenticEvent.phase)
        assertFalse(Gson().toJson(agenticEvent).contains("sensitive prompt"))
        assertFalse(Gson().toJson(agenticEvent).contains("secret"))
        assertEquals(0, received.get().raw.size())
        assertEquals("run-android", received.get().agenticEvent?.run_id)
        assertFalse(Gson().toJson(received.get()).contains("sensitive prompt"))
        assertFalse(Gson().toJson(received.get()).contains("secret"))
        socket.disconnect()
        assertTrue(closed.await(3, TimeUnit.SECONDS))
    }

    @Test
    fun mapsHandshake401ToAuthenticationFailureWithoutReconnect() {
        server.enqueue(MockResponse().setResponseCode(401))
        val authenticationFailed = CountDownLatch(1)
        val socket = JarvisChatWebSocket(context, httpClient)
        socket.setListener(
            object : ChatWebSocketListener {
                override fun onWsMessage(message: WsIncomingMessage) = Unit

                override fun onWsConnectionState(state: WsConnectionState) {
                    if (state == WsConnectionState.AuthenticationFailed) {
                        authenticationFailed.countDown()
                    }
                }
            },
        )

        socket.connect()
        assertTrue(authenticationFailed.await(3, TimeUnit.SECONDS))
        assertEquals(WsConnectionState.AuthenticationFailed, socket.connectionState.value)
        socket.disconnect()
    }
}
