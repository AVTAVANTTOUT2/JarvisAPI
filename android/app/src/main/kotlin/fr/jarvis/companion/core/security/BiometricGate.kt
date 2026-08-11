package fr.jarvis.companion.core.security

import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

internal object BiometricPolicy {
    fun authenticators(sdk: Int): Int =
        if (sdk >= Build.VERSION_CODES.R) {
            BiometricManager.Authenticators.BIOMETRIC_STRONG or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL
        } else {
            // Android 9–10 ne supporte pas STRONG | DEVICE_CREDENTIAL.
            BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL
        }
}

/** Verrou interactif de l'application ; les workers H24 restent autonomes. */
class BiometricGate(
    private val activity: FragmentActivity,
    private val onSuccess: () -> Unit,
    private val onError: (String) -> Unit,
) {
    private val authenticators = BiometricPolicy.authenticators(Build.VERSION.SDK_INT)
    private val manager = BiometricManager.from(activity)
    private val prompt = BiometricPrompt(
        activity,
        ContextCompat.getMainExecutor(activity),
        object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                isAuthenticating = false
                onSuccess()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                isAuthenticating = false
                onError(errString.toString())
            }

            override fun onAuthenticationFailed() {
                onError("Biométrie non reconnue. Réessayez.")
            }
        },
    )

    var isAuthenticating: Boolean = false
        private set

    fun isAvailable(): Boolean =
        manager.canAuthenticate(authenticators) == BiometricManager.BIOMETRIC_SUCCESS

    fun authenticate() {
        if (isAuthenticating) return
        if (!isAvailable()) {
            onError("Configurez la biométrie ou le verrouillage de l’appareil pour protéger JARVIS.")
            return
        }
        isAuthenticating = true
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("Déverrouiller JARVIS")
                .setSubtitle("Accéder à votre intelligence personnelle")
                .setAllowedAuthenticators(authenticators)
                .setConfirmationRequired(false)
                .build(),
        )
    }
}
