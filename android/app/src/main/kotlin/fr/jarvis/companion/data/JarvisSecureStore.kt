package fr.jarvis.companion.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.security.Security
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Secrets chiffrés via Android Keystore (AES-GCM, clé non exportable). */
class JarvisSecureStore(context: Context) {
    private val preferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun put(name: String, value: String) {
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.ENCRYPT_MODE, key())
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
                key(),
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

    private fun key(): SecretKey {
        if (Security.getProvider(ANDROID_KEYSTORE) == null) {
            // JVM de test (Robolectric) : AndroidKeyStore n'existe pas. Clé AES
            // éphémère en mémoire — jamais atteint sur un appareil réel.
            return testKey ?: SecretKeySpec(ByteArray(32) { it.toByte() }, "AES").also { testKey = it }
        }
        val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (store.containsAlias(ALIAS)) {
            return (store.getEntry(ALIAS, null) as KeyStore.SecretKeyEntry).secretKey
        }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        @Volatile private var testKey: SecretKey? = null
        private const val PREFS = "jarvis_secure"
        private const val ALIAS = "jarvis_companion_v1"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val CIPHER = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS = 128
    }
}
