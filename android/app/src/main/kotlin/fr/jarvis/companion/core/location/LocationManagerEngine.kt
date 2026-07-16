package fr.jarvis.companion.core.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper

class LocationManagerEngine(
    private val context: Context,
) : LocationEngine, LocationListener {
    private var locationManager: LocationManager? = null
    private var listener: LocationEngine.Listener? = null
    private var lastLocation: CapturedLocation? = null

    override fun start(config: LocationRequestConfig, listener: LocationEngine.Listener) {
        stop()
        if (!hasFineLocation()) {
            LocationRuntimeDiagnostics.logWarn("Location engine start skipped: fine permission missing")
            return
        }
        this.listener = listener
        val manager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        locationManager = manager

        val gpsEnabled = manager.isProviderEnabled(LocationManager.GPS_PROVIDER)
        val networkEnabled = manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        LocationRuntimeDiagnostics.gpsProviderEnabled.set(gpsEnabled)
        LocationRuntimeDiagnostics.networkProviderEnabled.set(networkEnabled)

        if (!gpsEnabled && !networkEnabled) {
            LocationRuntimeDiagnostics.logWarn("Location engine start skipped: no provider enabled")
            return
        }

        val now = System.currentTimeMillis()
        seedLastKnown(manager, LocationManager.GPS_PROVIDER, now)
        seedLastKnown(manager, LocationManager.NETWORK_PROVIDER, now)

        val looper = Looper.getMainLooper()
        try {
            if (networkEnabled) {
                manager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER,
                    config.minTimeMs,
                    config.minDistanceMeters,
                    this,
                    looper,
                )
            }
            if (gpsEnabled) {
                manager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER,
                    config.minTimeMs,
                    config.minDistanceMeters,
                    this,
                    looper,
                )
            }
            LocationRuntimeDiagnostics.engineStarted.set(true)
            LocationRuntimeDiagnostics.logInfo(
                "Location engine started (gps=$gpsEnabled network=$networkEnabled " +
                    "interval=${config.minTimeMs}ms dist=${config.minDistanceMeters}m)",
            )
        } catch (ex: SecurityException) {
            LocationRuntimeDiagnostics.engineStarted.set(false)
            LocationRuntimeDiagnostics.logWarn("Location requestLocationUpdates denied: ${ex.message}")
            stop()
        }
    }

    override fun stop() {
        try {
            locationManager?.removeUpdates(this)
        } catch (ex: SecurityException) {
            LocationRuntimeDiagnostics.logWarn("Location removeUpdates denied: ${ex.message}")
        }
        locationManager = null
        listener = null
        LocationRuntimeDiagnostics.engineStarted.set(false)
    }

    override fun lastKnown(): CapturedLocation? = lastLocation

    override fun onLocationChanged(location: Location) {
        LocationRuntimeDiagnostics.onCallback()
        val captured = location.toCaptured()
        lastLocation = captured
        listener?.onLocation(captured)
    }

    @Deprecated("Deprecated in API")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit

    override fun onProviderEnabled(provider: String) {
        when (provider) {
            LocationManager.GPS_PROVIDER ->
                LocationRuntimeDiagnostics.gpsProviderEnabled.set(true)
            LocationManager.NETWORK_PROVIDER ->
                LocationRuntimeDiagnostics.networkProviderEnabled.set(true)
        }
    }

    override fun onProviderDisabled(provider: String) {
        when (provider) {
            LocationManager.GPS_PROVIDER ->
                LocationRuntimeDiagnostics.gpsProviderEnabled.set(false)
            LocationManager.NETWORK_PROVIDER ->
                LocationRuntimeDiagnostics.networkProviderEnabled.set(false)
        }
    }

    private fun seedLastKnown(manager: LocationManager, provider: String, now: Long) {
        if (!manager.isProviderEnabled(provider)) return
        try {
            val raw = manager.getLastKnownLocation(provider) ?: return
            val captured = raw.toCaptured()
            if (lastLocation == null || captured.capturedAt >= (lastLocation?.capturedAt ?: 0L)) {
                lastLocation = captured
            }
            val age = now - captured.capturedAt
            if (age in 0..LocationConstants.MAX_LAST_KNOWN_AGE_MS) {
                LocationRuntimeDiagnostics.onCallback()
                listener?.onLocation(captured)
            }
        } catch (ex: SecurityException) {
            LocationRuntimeDiagnostics.logWarn("getLastKnownLocation($provider) denied: ${ex.message}")
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
