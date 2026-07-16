package fr.jarvis.companion.core.location

/**
 * Cadence de capture choisie par l'utilisateur.
 * Live = 1 min / 0 m (défaut produit).
 */
enum class CaptureCadenceMode {
    LIVE,
    BALANCED,
    ECONOMY,
    ;

    fun labelFr(): String = when (this) {
        LIVE -> "Live — 1 minute"
        BALANCED -> "Équilibré — 5 minutes"
        ECONOMY -> "Économe — 15 minutes"
    }

    fun toRequestConfig(): LocationRequestConfig = when (this) {
        LIVE -> LocationRequestConfig(
            minTimeMs = LocationConstants.LIVE_MIN_TIME_MS,
            minDistanceMeters = LocationConstants.LIVE_MIN_DISTANCE_METERS,
        )
        BALANCED -> LocationRequestConfig(
            minTimeMs = LocationConstants.BALANCED_MIN_TIME_MS,
            minDistanceMeters = LocationConstants.BALANCED_MIN_DISTANCE_METERS,
        )
        ECONOMY -> LocationRequestConfig(
            minTimeMs = LocationConstants.ECONOMY_MIN_TIME_MS,
            minDistanceMeters = LocationConstants.ECONOMY_MIN_DISTANCE_METERS,
        )
    }

    fun maxAccuracyMeters(): Float = when (this) {
        LIVE -> LocationConstants.LIVE_MAX_ACCURACY_METERS
        BALANCED -> LocationConstants.BALANCED_MAX_ACCURACY_METERS
        ECONOMY -> LocationConstants.ECONOMY_MAX_ACCURACY_METERS
    }

    fun heartbeatIntervalMs(): Long = when (this) {
        LIVE -> LocationConstants.HEARTBEAT_INTERVAL_MS
        BALANCED -> LocationConstants.BALANCED_MIN_TIME_MS
        ECONOMY -> LocationConstants.ECONOMY_MIN_TIME_MS
    }

    companion object {
        fun fromPrefs(raw: String?): CaptureCadenceMode =
            entries.firstOrNull { it.name == raw } ?: LIVE
    }
}
