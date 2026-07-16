package fr.jarvis.companion.core.location

data class LocationRequestConfig(
    val minTimeMs: Long,
    val minDistanceMeters: Float,
)

data class CapturedLocation(
    val latitude: Double,
    val longitude: Double,
    val altitude: Double?,
    val accuracy: Float,
    val speed: Float?,
    val bearing: Float?,
    val provider: String?,
    val capturedAt: Long,
)

interface LocationEngine {
    fun interface Listener {
        fun onLocation(location: CapturedLocation)
    }

    fun start(config: LocationRequestConfig, listener: Listener)
    fun stop()
    fun lastKnown(): CapturedLocation?
}
