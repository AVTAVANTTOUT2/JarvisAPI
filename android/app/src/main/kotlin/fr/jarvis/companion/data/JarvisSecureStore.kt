package fr.jarvis.companion.data

import android.content.Context
import android.util.Base64
import java.nio.charset.StandardCharsets
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec

/** Secrets chiffrés (AES-GCM). La clé vient d'un [SecretKeyProvider] injectable. */
class JarvisSecureStore(
    context: Context,
    private val keyProvider: SecretKeyProvider = defaultKeyProvider,
) {
    private val preferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun put(name: String, value: String) {
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.ENCRYPT_MODE, keyProvider.secretKey())
        val encrypted = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        val payload = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) +
            "." + Base64.encodeToString(encrypted, Base64.NO_WRAP)
        preferences.edit().putString(name, payload).apply()
    }

    @Synchronized
    fun get(name: String): String {
        val payload = preferences.getString(name, "") ?: return ""
        if (payload.isEmpty()) return ""
        return try {
            val parts = payload.split(".", limit = 2)
            if (parts.size != 2) return ""
            val cipher = Cipher.getInstance(CIPHER)
            cipher.init(
                Cipher.DECRYPT_MODE,
                keyProvider.secretKey(),
                GCMParameterSpec(GCM_TAG_BITS, Base64.decode(parts[0], Base64.NO_WRAP)),
            )
            String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8)
        } catch (_: Exception) {
            preferences.edit().remove(name).apply()
            ""
        }
    }

    @Synchronized
    fun remove(name: String) {
        preferences.edit().remove(name).apply()
    }

    companion object {
        /**
         * Fournisseur par défaut, remplaçable par les tests (Robolectric n'a pas
         * d'AndroidKeyStore). Aucune clé de test ne vit dans le code de production.
         */
        @Volatile var defaultKeyProvider: SecretKeyProvider = AndroidKeyStoreProvider()

        private const val PREFS = "jarvis_secure"
        private const val CIPHER = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS = 128
    }
}
