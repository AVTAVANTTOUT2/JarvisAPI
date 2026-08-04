package fr.jarvis.companion.services

enum class LocationServiceDecision {
    STOP,
    SYNC_CACHED,
    START_CAPTURE,
    REJECT_MISSING_PREREQUISITE,
}

fun locationServiceDecision(
    action: String?,
    hasNativeToken: Boolean,
    hasLocationPermission: Boolean,
): LocationServiceDecision = when {
    action == JarvisLocationService.ACTION_STOP -> LocationServiceDecision.STOP
    !hasNativeToken -> LocationServiceDecision.REJECT_MISSING_PREREQUISITE
    action == JarvisLocationService.ACTION_SYNC -> LocationServiceDecision.SYNC_CACHED
    !hasLocationPermission -> LocationServiceDecision.REJECT_MISSING_PREREQUISITE
    else -> LocationServiceDecision.START_CAPTURE
}

fun canStartWakeWord(accessKey: String, hasRecordAudioPermission: Boolean): Boolean =
    accessKey.isNotBlank() && hasRecordAudioPermission

fun notificationChannelForPriority(priority: String): String =
    when (priority.trim().lowercase()) {
        "urgent", "high" -> fr.jarvis.companion.notifications.JarvisNotifications.URGENT
        else -> fr.jarvis.companion.notifications.JarvisNotifications.DEFAULT
    }
