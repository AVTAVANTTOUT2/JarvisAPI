package fr.jarvis.companion.services

import android.Manifest
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import fr.jarvis.companion.data.JarvisRepository
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Présence GPS économe via service de premier plan. */
class JarvisLocationService : Service(), LocationListener {
    private var locationManager: LocationManager? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val repository by lazy { JarvisRepository(this) }

    override fun onCreate() {
        super.onCreate()
        JarvisNotifications.createChannels(this)
        val notification = JarvisNotifications.foreground(
            this,
            JarvisNotifications.PRESENCE,
            "JARVIS connaît ta position",
            "Présence GPS active, fréquence économe",
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        locationManager = getSystemService(LOCATION_SERVICE) as LocationManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!hasLocationPermission() || JarvisSettings.nativeToken(this).isEmpty()) {
            stopSelf()
            return START_NOT_STICKY
        }
        requestUpdates()
        return START_STICKY
    }

    private fun hasLocationPermission(): Boolean =
        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun requestUpdates() {
        val manager = locationManager ?: return
        if (!hasLocationPermission()) return
        try {
            if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER,
                    MIN_TIME_MS,
                    MIN_DISTANCE_METERS,
                    this,
                )
            }
            if (manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                manager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER,
                    MIN_TIME_MS,
                    MIN_DISTANCE_METERS,
                    this,
                )
            }
        } catch (_: SecurityException) {
            stopSelf()
        }
    }

    override fun onLocationChanged(location: Location) {
        scope.launch {
            repository.postLocation(
                location.latitude,
                location.longitude,
                location.altitude,
                location.accuracy,
                location.speed,
                location.time,
            )
        }
    }

    @Deprecated("Deprecated in API")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit

    override fun onProviderEnabled(provider: String) = Unit
    override fun onProviderDisabled(provider: String) = Unit
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        locationManager?.removeUpdates(this)
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 4101
        private const val MIN_TIME_MS = 5 * 60 * 1000L
        private const val MIN_DISTANCE_METERS = 50f
    }
}
