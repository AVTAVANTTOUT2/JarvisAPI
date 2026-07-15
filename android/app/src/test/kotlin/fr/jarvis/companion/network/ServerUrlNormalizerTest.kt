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
        assertEquals("https://100.123.50.38:8081", ServerUrlNormalizer.normalize("https://100.123.50.38:8081/"))
    }

    @Test
    fun normalize_rejectsHttpAndEmpty() {
        assertNull(ServerUrlNormalizer.normalize(""))
        assertNull(ServerUrlNormalizer.normalize("http://10.0.2.2:8081"))
        assertNull(ServerUrlNormalizer.normalize("ftp://host"))
    }

    @Test
    fun isJarvisHost_matchesConfiguredHost() {
        assertTrue(ServerUrlNormalizer.isJarvisHost("https://10.0.2.2:8081", "10.0.2.2"))
        assertFalse(ServerUrlNormalizer.isJarvisHost("https://10.0.2.2:8081", "evil.local"))
    }
}
