package fr.jarvis.companion.core.location

enum class AdaptiveLocationMode {
    MOVING,
    STATIONARY,
    LOW_BATTERY,
}

class AdaptiveLocationPolicy(
    private var lowBatteryMode: Boolean = false,
    private var economyPreference: Boolean = false,
) {
    private var lastLatitude: Double? = null
    private var lastLongitude: Double? = null
    private var lastCapturedAt: Long = 0L

    fun setLowBattery(low: Boolean) {
        lowBatteryMode = low
    }

    fun setEconomyPreference(enabled: Boolean) {
        economyPreference = enabled
    }

    fun onLocationRetained(location: CapturedLocation) {
        lastLatitude = location.latitude
        lastLongitude = location.longitude
        lastCapturedAt = location.capturedAt
    }

    fun currentMode(): AdaptiveLocationMode = when {
        lowBatteryMode || economyPreference -> AdaptiveLocationMode.LOW_BATTERY
        isStationary() -> AdaptiveLocationMode.STATIONARY
        else -> AdaptiveLocationMode.MOVING
    }

    fun currentConfig(): LocationRequestConfig = when (currentMode()) {
        AdaptiveLocationMode.MOVING -> LocationRequestConfig(
            minTimeMs = LocationConstants.MIN_TIME_MOVING_MS,
            minDistanceMeters = LocationConstants.MIN_DISTANCE_MOVING_METERS,
        )
        AdaptiveLocationMode.STATIONARY -> LocationRequestConfig(
            minTimeMs = LocationConstants.MIN_TIME_STATIONARY_MS,
            minDistanceMeters = LocationConstants.MIN_DISTANCE_STATIONARY_METERS,
        )
        AdaptiveLocationMode.LOW_BATTERY -> LocationRequestConfig(
            minTimeMs = LocationConstants.MIN_TIME_LOW_BATTERY_MS,
            minDistanceMeters = LocationConstants.MIN_DISTANCE_LOW_BATTERY_METERS,
        )
    }

    private fun isStationary(): Boolean {
        val lat = lastLatitude ?: return false
        val lng = lastLongitude ?: return false
        if (lastCapturedAt <= 0L) return false
        val elapsed = System.currentTimeMillis() - lastCapturedAt
        if (elapsed < LocationConstants.MIN_TIME_STATIONARY_MS) return false
        return true
    }
}
