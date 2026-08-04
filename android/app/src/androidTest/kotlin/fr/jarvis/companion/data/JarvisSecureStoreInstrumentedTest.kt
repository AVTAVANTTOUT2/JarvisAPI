package fr.jarvis.companion.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class JarvisSecureStoreInstrumentedTest {
    private lateinit var context: Context
    private lateinit var store: JarvisSecureStore
    private val key = "instrumented_secret"

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        JarvisSecureStore.defaultKeyProvider = AndroidKeyStoreProvider()
        store = JarvisSecureStore(context)
        store.remove(key)
    }

    @After
    fun tearDown() {
        store.remove(key)
    }

    @Test
    fun androidKeystoreEncryptsAndDecryptsWithoutPersistingPlaintext() {
        val secret = "jeton-mobile-haute-entropie"

        store.put(key, secret)

        assertEquals(secret, store.get(key))
        val persisted = context.getSharedPreferences("jarvis_secure", Context.MODE_PRIVATE)
            .getString(key, "")
            .orEmpty()
        assertTrue(persisted.contains("."))
        assertNotEquals(secret, persisted)
        assertFalse(persisted.contains(secret))
    }

    @Test
    fun tamperedCiphertextIsRejectedAndRemoved() {
        store.put(key, "secret")
        context.getSharedPreferences("jarvis_secure", Context.MODE_PRIVATE)
            .edit()
            .putString(key, "iv.chiffrement-modifie")
            .commit()

        assertEquals("", store.get(key))
        assertFalse(
            context.getSharedPreferences("jarvis_secure", Context.MODE_PRIVATE)
                .contains(key),
        )
    }
}
