package fr.jarvis.companion.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerUrlNormalizerTest {
    @Test
    fun normalize_addsHttpsAndStripsPath() {
        assertEquals("https://10.0.2.2:8081", ServerUrlNormalizer.normalize("10.0.2.2:8081"))
        assertEquals("https://192.168.1.10:8081", ServerUrlNormalizer.normalize("192.168.1.10:8081"))
        assertEquals("https://jarvis.local:8443", ServerUrlNormalizer.normalize("https://jarvis.local:8443/"))
    }

    @Test
    fun normalize_rejectsHttpAndEmpty() {
        assertNull(ServerUrlNormalizer.normalize(""))
        assertNull(ServerUrlNormalizer.normalize("   "))
        assertNull(ServerUrlNormalizer.normalize("http://10.0.2.2:8081"))
        assertNull(ServerUrlNormalizer.normalize("ftp://host"))
        assertNull(ServerUrlNormalizer.normalize("ws://host"))
    }

    @Test
    fun normalize_customPortAndIpv4() {
        assertEquals("https://10.0.2.2:9443", ServerUrlNormalizer.normalize("10.0.2.2:9443"))
        assertEquals("https://127.0.0.1:8081", ServerUrlNormalizer.normalize("127.0.0.1:8081"))
    }

    @Test
    fun normalize_rejectsInvalidHost() {
        assertNull(ServerUrlNormalizer.normalize("https://"))
        assertNull(ServerUrlNormalizer.normalize("not a url !!!"))
    }

    @Test
    fun isJarvisHost_matchesConfiguredHost() {
        assertTrue(ServerUrlNormalizer.isJarvisHost("https://10.0.2.2:8081", "10.0.2.2"))
        assertTrue(ServerUrlNormalizer.isJarvisHost("https://100.64.0.5:8081", "100.64.0.5"))
        assertFalse(ServerUrlNormalizer.isJarvisHost("https://10.0.2.2:8081", "evil.local"))
    }
}
