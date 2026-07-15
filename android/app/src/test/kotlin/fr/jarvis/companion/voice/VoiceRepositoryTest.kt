package fr.jarvis.companion.voice

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.ServerUrlNormalizer
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class VoiceRepositoryTest {
    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var httpClient: OkHttpClient

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        val localhostCertificate = HeldCertificate.Builder()
            .addSubjectAlternativeName("localhost")
            .build()
        val serverCertificates = HandshakeCertificates.Builder()
            .heldCertificate(localhostCertificate)
            .build()
        server = MockWebServer()
        server.useHttps(serverCertificates.sslSocketFactory(), false)
        server.start()
        httpClient = OkHttpClient.Builder()
            .sslSocketFactory(serverCertificates.sslSocketFactory(), serverCertificates.trustManager)
            .hostnameVerifier { _, _ -> true }
            .build()
        JarvisSettings.setServer(context, server.url("/").toString().removeSuffix("/"))
        JarvisSettings.setNativeToken(context, "test-token-123")
    }

    @After
    fun tearDown() {
        server.shutdown()
        JarvisSettings.clearNativeToken(context)
    }

    @Test
    fun rejectsHttpServerUrl() {
        JarvisSettings.setServer(context, "http://insecure.local")
        val repo = VoiceRepository(context, httpClient)
        assertFalse(repo.isHttpsConfigured())
    }

    @Test
    fun requiresNativeToken() {
        JarvisSettings.clearNativeToken(context)
        val repo = VoiceRepository(context, httpClient)
        assertFalse(repo.hasToken())
    }

    @Test
    fun sendsBearerAuthorization() {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"conversation_id":1,"transcript":"bonjour","response_text":"Bonjour.","audio_base64":null,"audio_mime_type":null,"stt_engine":"faster-whisper","stt_model":"small","tts_engine":"kokoro","source":"android_voice","device_id":"android-test"}""",
                ),
        )
        val audio = File.createTempFile("voice", ".m4a", context.cacheDir)
        audio.writeBytes(m4aPlaceholder())
        val repo = VoiceRepository(context, httpClient)
        val result = kotlinx.coroutines.runBlocking {
            repo.sendVoiceTurn(audio, null)
        }
        audio.delete()
        assertTrue(result is VoiceApiResult.Success)
        val auth = server.takeRequest().getHeader("Authorization")
        assertEquals("Bearer test-token-123", auth)
        assertTrue(ServerUrlNormalizer.normalize(repo.serverUrl())!!.startsWith("https://"))
    }

    @Test
    fun mapsHttp401ToFailure() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Jeton mobile invalide"}"""))
        val audio = File.createTempFile("voice", ".m4a", context.cacheDir)
        audio.writeBytes(m4aPlaceholder())
        val repo = VoiceRepository(context, httpClient)
        val result = kotlinx.coroutines.runBlocking {
            repo.sendVoiceTurn(audio, null)
        }
        audio.delete()
        assertTrue(result is VoiceApiResult.Failure)
        assertEquals(401, (result as VoiceApiResult.Failure).httpCode)
    }

    private fun m4aPlaceholder(): ByteArray {
        val bytes = ByteArray(1200)
        bytes[4] = 'f'.code.toByte()
        bytes[5] = 't'.code.toByte()
        bytes[6] = 'y'.code.toByte()
        bytes[7] = 'p'.code.toByte()
        return bytes
    }
}
