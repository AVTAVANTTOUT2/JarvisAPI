package fr.jarvis.companion.network

import android.content.Context
import fr.jarvis.companion.R
import java.security.KeyStore
import java.security.cert.CertificateFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * Confiance à la CA privée JARVIS pour l'hôte serveur configuré (pas du certificate pinning).
 * Les autres hôtes conservent la validation CA système via [network_security_config].
 */
object JarvisTls {
    @Volatile
    private var cachedSslContext: SSLContext? = null

    @Volatile
    private var trustManagerCache: X509TrustManager? = null

    fun sslContext(context: Context): SSLContext {
        val tm = serverTrustManager(context)
        return cachedSslContext ?: synchronized(this) {
            cachedSslContext ?: SSLContext.getInstance("TLS").apply {
                init(null, arrayOf(tm), null)
            }.also { cachedSslContext = it }
        }
    }

    fun serverTrustManager(context: Context): X509TrustManager {
        return trustManagerCache ?: synchronized(this) {
            trustManagerCache ?: buildServerTrustManager(context.applicationContext).also {
                trustManagerCache = it
            }
        }
    }

    private fun buildServerTrustManager(appContext: Context): X509TrustManager {
        val system = defaultTrustManager()
        val jarvis = jarvisTrustManager(appContext)
        return CompositeTrustManager(system, jarvis)
    }

    private fun defaultTrustManager(): X509TrustManager {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(null as KeyStore?)
        return tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
    }

    private fun jarvisTrustManager(appContext: Context): X509TrustManager {
        val cf = CertificateFactory.getInstance("X.509")
        appContext.resources.openRawResource(R.raw.jarvis_ca).use { stream ->
            val cert = cf.generateCertificate(stream)
            val keyStore = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
                load(null, null)
                setCertificateEntry("jarvis_ca", cert)
            }
            val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            tmf.init(keyStore)
            return tmf.trustManagers.filterIsInstance<X509TrustManager>().first()
        }
    }

    private class CompositeTrustManager(
        private val system: X509TrustManager,
        private val jarvis: X509TrustManager,
    ) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) {
            system.checkClientTrusted(chain, authType)
        }

        override fun checkServerTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) {
            try {
                jarvis.checkServerTrusted(chain, authType)
            } catch (_: Exception) {
                system.checkServerTrusted(chain, authType)
            }
        }

        override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> =
            system.acceptedIssuers
    }
}
