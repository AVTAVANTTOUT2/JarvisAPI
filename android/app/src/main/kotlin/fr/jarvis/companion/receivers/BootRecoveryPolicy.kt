package fr.jarvis.companion.receivers

import android.content.Intent

data class BootRecoveryPlan(
    val scheduleLocationSync: Boolean,
    val startLocationService: Boolean,
    val showWakeWordReminder: Boolean,
)

fun isSupportedBootAction(action: String?): Boolean = action in setOf(
    Intent.ACTION_BOOT_COMPLETED,
    Intent.ACTION_MY_PACKAGE_REPLACED,
)

fun bootRecoveryPlan(
    hasNativeToken: Boolean,
    locationEnabled: Boolean,
    hasLocationPermission: Boolean,
    wakeWordEnabled: Boolean,
): BootRecoveryPlan = BootRecoveryPlan(
    scheduleLocationSync = hasNativeToken,
    startLocationService = hasNativeToken && locationEnabled && hasLocationPermission,
    showWakeWordReminder = wakeWordEnabled,
)
