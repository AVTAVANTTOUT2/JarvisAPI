package fr.jarvis.companion.core.security

import androidx.biometric.BiometricManager
import org.junit.Assert.assertEquals
import org.junit.Test

class BiometricPolicyTest {
    @Test
    fun `android 9 and 10 use a supported credential combination`() {
        assertEquals(
            BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL,
            BiometricPolicy.authenticators(29),
        )
    }

    @Test
    fun `android 11 and newer prefer strong biometrics`() {
        assertEquals(
            BiometricManager.Authenticators.BIOMETRIC_STRONG or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL,
            BiometricPolicy.authenticators(30),
        )
    }
}
