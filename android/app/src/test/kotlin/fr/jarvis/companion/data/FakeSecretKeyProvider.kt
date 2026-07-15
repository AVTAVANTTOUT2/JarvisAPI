package fr.jarvis.companion.data

import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/** Clé AES en mémoire pour les tests JVM — jamais empaquetée dans l'APK. */
class FakeSecretKeyProvider : SecretKeyProvider {
    private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    override fun secretKey(): SecretKey = key
}
