package fr.jarvis.companion.network

import org.junit.Assert.assertEquals
import org.junit.Test

class JarvisHttpClientTest {
    @Test
    fun normalizeBaseUrl_addsTrailingSlash() {
        assertEquals("https://10.0.2.2:8081/", JarvisHttpClient.normalizeBaseUrl("https://10.0.2.2:8081"))
        assertEquals("https://127.0.0.1:8081/", JarvisHttpClient.normalizeBaseUrl("https://127.0.0.1:8081/"))
    }
}
