package fr.jarvis.companion.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class JarvisHttpClientTest {
    @Test
    fun normalizeBaseUrl_addsTrailingSlash() {
        assertEquals("https://10.0.2.2:8081/", JarvisHttpClient.normalizeBaseUrl("https://10.0.2.2:8081"))
        assertEquals("https://127.0.0.1:8081/", JarvisHttpClient.normalizeBaseUrl("https://127.0.0.1:8081/"))
    }

    @Test
    fun normalizeBaseUrl_rejectsCleartextAndMalformedServers() {
        for (server in listOf("http://jarvis.local", "ws://jarvis.local", "https://")) {
            assertThrows(IllegalArgumentException::class.java) {
                JarvisHttpClient.normalizeBaseUrl(server)
            }
        }
    }

    @Test
    fun normalizeBaseUrl_stripsPathsAndQueries() {
        assertEquals(
            "https://jarvis.local:8443/",
            JarvisHttpClient.normalizeBaseUrl("https://jarvis.local:8443/api?token=secret"),
        )
    }
}
