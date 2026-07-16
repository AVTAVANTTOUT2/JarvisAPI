package fr.jarvis.companion.services

import android.Manifest
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.location.AdaptiveLocationMode
import fr.jarvis.companion.core.location.CapturedLocation
import fr.jarvis.companion.core.location.LocationConstants
import fr.jarvis.companion.core.location.LocationValidator
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/** Capture GPS offline-first : Room puis sync différée. */
class JarvisLocationService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var observeJob: Job? = null
    private var batteryReceiver: BroadcastReceiver? = null

    private val container by lazy { appContainer() }

    override fun onCreate() {
        super.onCreate()
        JarvisNotifications.createChannels(this)
        startForegroundWithPending(0)
        registerBatteryReceiver()
        startPendingObserver()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                JarvisSettings.setLocationEnabled(this, false)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SYNC -> {
                container.locationSyncCoordinator.requestSync(this)
                return START_STICKY
            }
        }

        if (!hasLocationPermission() || JarvisSettings.nativeToken(this).isEmpty()) {
            stopSelf()
            return START_NOT_STICKY
        }

        startEngine()
        return START_STICKY
    }

    private fun startEngine() {
        val policy = container.adaptiveLocationPolicy
        val config = policy.currentConfig()
        container.locationEngine.start(config) { location ->
            scope.launch { handleLocation(location) }
        }
    }

    private suspend fun handleLocation(location: CapturedLocation) {
        val policy = container.adaptiveLocationPolicy
        val lowBattery = isLowBattery()
        policy.setLowBattery(lowBattery)

        val validator = if (policy.currentMode() == AdaptiveLocationMode.LOW_BATTERY) {
            LocationValidator(LocationValidator.economyConfig())
        } else {
            container.locationValidator
        }

        val validation = validator.validate(location)
        if (!validation.valid) {
            container.pendingLocationStore.enqueue(
                location = location,
                syncState = PendingLocationSyncState.INVALID,
                errorCode = validation.errorCode,
                errorMessage = validation.errorMessage,
            )
            return
        }

        val recent = container.pendingLocationStore.getRecentForDedup()
        if (!container.locationDeduplicator.shouldKeep(location, recent)) {
            return
        }

        container.pendingLocationStore.enqueue(location)
        policy.onLocationRetained(location)
        container.locationSyncCoordinator.recordCaptured()
        container.locationSyncCoordinator.requestSync(this@JarvisLocationService)

        val newConfig = policy.currentConfig()
        container.locationEngine.stop()
        container.locationEngine.start(newConfig) { loc ->
            scope.launch { handleLocation(loc) }
        }
    }

    private fun startPendingObserver() {
        observeJob?.cancel()
        val dao = container.database.pendingLocationDao()
        observeJob = scope.launch {
            combine(
                dao.observeCountByState(PendingLocationSyncState.PENDING),
                dao.observeCountByState(PendingLocationSyncState.FAILED_RETRYABLE),
            ) { pending, retryable -> pending + retryable }
                .collect { count -> updateForegroundNotification(count) }
        }
    }

    private fun updateForegroundNotification(pendingCount: Int) {
        val notification = JarvisNotifications.locationForeground(this, pendingCount)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startForegroundWithPending(pendingCount: Int) {
        updateForegroundNotification(pendingCount)
    }

    private fun registerBatteryReceiver() {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                container.adaptiveLocationPolicy.setLowBattery(isLowBattery())
            }
        }
        batteryReceiver = receiver
        registerReceiver(receiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    }

    private fun isLowBattery(): Boolean {
        val batteryIntent = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?: return false
        val level = batteryIntent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = batteryIntent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val percent = if (scale > 0) (level * 100) / scale else 100
        val status = batteryIntent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
            status == BatteryManager.BATTERY_STATUS_FULL
        return percent < LocationConstants.LOW_BATTERY_PERCENT && !charging
    }

    private fun hasLocationPermission(): Boolean =
        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        observeJob?.cancel()
        batteryReceiver?.let { unregisterReceiver(it) }
        container.locationEngine.stop()
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "fr.jarvis.companion.location.STOP"
        const val ACTION_SYNC = "fr.jarvis.companion.location.SYNC"
        private const val NOTIFICATION_ID = 4101
    }
}
