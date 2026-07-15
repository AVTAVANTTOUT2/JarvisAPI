package fr.jarvis.companion.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/** Source de la clé AES du store sécurisé — remplaçable en test. */
fun interface SecretKeyProvider {
    fun secretKey(): SecretKey
}

/** Implémentation de production : clé non exportable dans l'Android Keystore. */
class AndroidKeyStoreProvider : SecretKeyProvider {
    override fun secretKey(): SecretKey {
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

    private companion object {
        const val ALIAS = "jarvis_companion_v1"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
    }
}
