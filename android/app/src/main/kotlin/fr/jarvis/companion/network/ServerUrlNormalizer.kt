package fr.jarvis.companion.network

import java.net.URI

/** Normalise une adresse serveur JARVIS en URL HTTPS canonique. */
object ServerUrlNormalizer {
    fun normalize(raw: String?): String? {
        var value = raw?.trim().orEmpty()
        if (value.isEmpty()) return null
        val lower = value.lowercase()
        if (lower.startsWith("http://") || lower.startsWith("ftp://") || lower.startsWith("ws://")) {
            return null
        }
        if (!value.startsWith("https://")) {
            value = "https://$value"
        }
        return try {
            val uri = URI(value)
            if (uri.host.isNullOrBlank() || uri.scheme?.equals("https", ignoreCase = true) != true) {
                null
            } else {
                "https://${uri.rawAuthority}"
            }
        } catch (_: Exception) {
            null
        }
    }

    fun isJarvisHost(serverUrl: String, candidateHost: String?): Boolean {
        if (candidateHost.isNullOrBlank()) return false
        return try {
            URI(serverUrl).host.equals(candidateHost, ignoreCase = true)
        } catch (_: Exception) {
            false
        }
    }
}
