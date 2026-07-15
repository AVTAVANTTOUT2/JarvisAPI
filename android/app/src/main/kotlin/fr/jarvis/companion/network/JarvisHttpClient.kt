package fr.jarvis.companion.network

import android.content.Context
import fr.jarvis.companion.BuildConfig
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/** Client OkHttp/Retrofit vers le serveur JARVIS configuré (CA privée, pas de TrustAll). */
class JarvisHttpClient(context: Context) {
    private val appContext = context.applicationContext

    private val okHttp: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_SEC, TimeUnit.SECONDS)
        .readTimeout(READ_TIMEOUT_SEC, TimeUnit.SECONDS)
        .sslSocketFactory(
            JarvisTls.sslContext(appContext).socketFactory,
            JarvisTls.serverTrustManager(appContext),
        )
        .addInterceptor { chain ->
            chain.proceed(
                chain.request().newBuilder()
                    .header("Accept", "application/json")
                    .header("User-Agent", "JARVIS-Android/${BuildConfig.VERSION_NAME}")
                    .build(),
            )
        }
        .build()

    @Volatile
    private var cachedBaseUrl: String? = null

    @Volatile
    private var cachedService: JarvisApiService? = null

    fun service(baseUrl: String): JarvisApiService {
        val normalized = normalizeBaseUrl(baseUrl)
        if (normalized != cachedBaseUrl || cachedService == null) {
            cachedBaseUrl = normalized
            cachedService = Retrofit.Builder()
                .baseUrl(normalized)
                .client(okHttp)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
                .create(JarvisApiService::class.java)
        }
        return cachedService!!
    }

    fun invalidateCache() {
        cachedBaseUrl = null
        cachedService = null
    }

    companion object {
        private const val CONNECT_TIMEOUT_SEC = 12L
        private const val READ_TIMEOUT_SEC = 20L

        fun normalizeBaseUrl(url: String): String {
            val trimmed = url.trim().trimEnd('/')
            return "$trimmed/"
        }
    }
}
