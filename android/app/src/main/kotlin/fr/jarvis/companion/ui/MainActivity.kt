package fr.jarvis.companion.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import fr.jarvis.companion.core.ui.components.JarvisBackground
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.feature.onboarding.OnboardingScreen
import fr.jarvis.companion.navigation.JarvisNavCallbacks
import fr.jarvis.companion.navigation.JarvisNavHost
import fr.jarvis.companion.notifications.JarvisNotifications
import fr.jarvis.companion.services.JarvisLocationService
import fr.jarvis.companion.services.JarvisWakeWordService
import fr.jarvis.companion.ui.theme.JarvisTheme

/** Point d'entrée — shell navigation Compose, services GPS/wake préservés. */
class MainActivity : ComponentActivity() {
    private var locationPendingEnable = false
    private var wakePendingEnable = false

    private val locationPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val fine = grants[Manifest.permission.ACCESS_FINE_LOCATION] == true
        if (locationPendingEnable && fine) {
            requestBackgroundLocationOrStart()
        }
        locationPendingEnable = false
    }

    private val backgroundLocationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (locationPendingEnable && granted) {
            enableLocationService()
        }
        locationPendingEnable = false
    }

    private val micPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (wakePendingEnable && granted) {
            enableWakeWordService()
        }
        wakePendingEnable = false
    }

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* best effort */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisNotifications.createChannels(this)
        requestNotificationPermissionIfNeeded()
        resumePersistentFeatures()

        setContent {
            JarvisTheme {
                var showOnboarding by remember {
                    mutableStateOf(
                        !JarvisSettings.isOnboardingComplete(this) ||
                            JarvisSettings.nativeToken(this).isEmpty(),
                    )
                }

                if (showOnboarding) {
                    JarvisBackground {
                        OnboardingScreen(
                            onComplete = {
                                showOnboarding = false
                                resumePersistentFeatures()
                            },
                        )
                    }
                } else {
                    JarvisNavHost(
                        callbacks = JarvisNavCallbacks(
                            onLocationToggle = { enabled -> toggleLocation(enabled) },
                            onWakeToggle = { enabled -> toggleWakeWord(enabled) },
                            onPorcupineKeySave = { key ->
                                JarvisSettings.setPorcupineAccessKey(this, key)
                                if (wakePendingEnable || JarvisSettings.isWakeWordEnabled(this)) {
                                    toggleWakeWord(true)
                                }
                            },
                            onNeedsOnboarding = { showOnboarding = true },
                        ),
                    )
                }
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun toggleLocation(enabled: Boolean) {
        if (!enabled) {
            JarvisSettings.setLocationEnabled(this, false)
            stopService(Intent(this, JarvisLocationService::class.java))
            return
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            locationPendingEnable = true
            locationPermissions.launch(
                arrayOf(
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION,
                ),
            )
            return
        }
        requestBackgroundLocationOrStart()
    }

    private fun requestBackgroundLocationOrStart() {
        locationPendingEnable = true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName"),
                ),
            )
            return
        }
        if (Build.VERSION.SDK_INT == Build.VERSION_CODES.Q &&
            checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            backgroundLocationPermission.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            return
        }
        enableLocationService()
    }

    private fun enableLocationService() {
        locationPendingEnable = false
        JarvisSettings.setLocationEnabled(this, true)
        startForegroundService(Intent(this, JarvisLocationService::class.java))
    }

    private fun toggleWakeWord(enabled: Boolean) {
        if (!enabled) {
            JarvisSettings.setWakeWordEnabled(this, false)
            stopService(Intent(this, JarvisWakeWordService::class.java))
            return
        }
        if (JarvisSettings.porcupineAccessKey(this).isEmpty()) {
            wakePendingEnable = true
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            wakePendingEnable = true
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        enableWakeWordService()
    }

    private fun enableWakeWordService() {
        wakePendingEnable = false
        JarvisSettings.setWakeWordEnabled(this, true)
        startForegroundService(Intent(this, JarvisWakeWordService::class.java))
    }

    private fun resumePersistentFeatures() {
        if (JarvisSettings.isLocationEnabled(this) &&
            checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startForegroundService(Intent(this, JarvisLocationService::class.java))
        }
        if (JarvisSettings.isWakeWordEnabled(this) &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED &&
            JarvisSettings.porcupineAccessKey(this).isNotEmpty()
        ) {
            startForegroundService(Intent(this, JarvisWakeWordService::class.java))
        }
    }
}
