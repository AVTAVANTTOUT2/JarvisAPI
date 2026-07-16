package fr.jarvis.companion.core.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle

class LocationManagerEngine(
    private val context: Context,
) : LocationEngine, LocationListener {
    private var locationManager: LocationManager? = null
    private var listener: LocationEngine.Listener? = null
    private var lastLocation: CapturedLocation? = null

    override fun start(config: LocationRequestConfig, listener: LocationEngine.Listener) {
        if (!hasFineLocation()) return
        this.listener = listener
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        locationManager = manager
        try {
            manager.getLastKnownLocation(LocationManager.GPS_PROVIDER)?.let { updateLast(it) }
            manager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)?.let { updateLast(it) }
            if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER,
                    config.minTimeMs,
                    config.minDistanceMeters,
                    this,
                )
            }
            if (manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER,
                    config.minTimeMs,
                    config.minDistanceMeters,
                    this,
                )
            }
        } catch (_: SecurityException) {
            stop()
        }
    }

    override fun stop() {
        locationManager?.removeUpdates(this)
        locationManager = null
        listener = null
    }

    override fun lastKnown(): CapturedLocation? = lastLocation

    override fun onLocationChanged(location: Location) {
        val captured = location.toCaptured()
        lastLocation = captured
        listener?.onLocation(captured)
    }

    @Deprecated("Deprecated in API")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit

    override fun onProviderEnabled(provider: String) = Unit
    override fun onProviderDisabled(provider: String) = Unit

    private fun updateLast(location: Location) {
        val captured = location.toCaptured()
        if (lastLocation == null || captured.capturedAt >= (lastLocation?.capturedAt ?: 0L)) {
            lastLocation = captured
        }
    }

    private fun hasFineLocation(): Boolean =
        context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun Location.toCaptured(): CapturedLocation = CapturedLocation(
        latitude = latitude,
        longitude = longitude,
        altitude = if (hasAltitude()) altitude else null,
        accuracy = accuracy,
        speed = if (hasSpeed()) speed else null,
        bearing = if (hasBearing()) bearing else null,
        provider = provider,
        capturedAt = time,
    )
}
