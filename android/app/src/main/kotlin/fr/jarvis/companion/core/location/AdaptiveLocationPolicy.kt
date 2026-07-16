package fr.jarvis.companion.core.location

enum class AdaptiveLocationMode {
    MOVING,
    STATIONARY,
    LOW_BATTERY,
}

class AdaptiveLocationPolicy(
    initialCadence: CaptureCadenceMode = CaptureCadenceMode.LIVE,
) {
    private var cadenceMode: CaptureCadenceMode = initialCadence
    private var lowBatteryMode: Boolean = false
    private var lastLatitude: Double? = null
    private var lastLongitude: Double? = null
    private var lastCapturedAt: Long = 0L

    fun setCadenceMode(mode: CaptureCadenceMode) {
        cadenceMode = mode
    }

    fun cadenceMode(): CaptureCadenceMode = cadenceMode

    fun setLowBattery(low: Boolean) {
        lowBatteryMode = low
    }

    fun effectiveCadence(): CaptureCadenceMode =
        if (lowBatteryMode) CaptureCadenceMode.ECONOMY else cadenceMode

    fun onLocationRetained(location: CapturedLocation) {
        lastLatitude = location.latitude
        lastLongitude = location.longitude
        lastCapturedAt = location.capturedAt
    }

    fun currentMode(): AdaptiveLocationMode = when {
        lowBatteryMode -> AdaptiveLocationMode.LOW_BATTERY
        isStationary() -> AdaptiveLocationMode.STATIONARY
        else -> AdaptiveLocationMode.MOVING
    }

    fun currentConfig(): LocationRequestConfig = effectiveCadence().toRequestConfig()

    fun validationConfig(): LocationValidationConfig =
        LocationValidator.forCadence(effectiveCadence())

    fun heartbeatIntervalMs(): Long = effectiveCadence().heartbeatIntervalMs()

    private fun isStationary(): Boolean {
        if (lastLatitude == null || lastLongitude == null || lastCapturedAt <= 0L) {
            return false
        }
        val elapsed = System.currentTimeMillis() - lastCapturedAt
        return elapsed >= LocationConstants.MIN_TIME_STATIONARY_MS
    }
}
