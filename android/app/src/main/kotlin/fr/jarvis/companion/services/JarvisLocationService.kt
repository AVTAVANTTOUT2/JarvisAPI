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
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.database.SyncMetadataEntity
import fr.jarvis.companion.core.location.CapturedLocation
import fr.jarvis.companion.core.location.LocationConstants
import fr.jarvis.companion.core.location.LocationRuntimeDiagnostics
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
    private val mainHandler = Handler(Looper.getMainLooper())
    private var observeJob: Job? = null
    private var batteryReceiver: BroadcastReceiver? = null
    private var engineStarted = false

    private val container by lazy { appContainer() }

    override fun onCreate() {
        super.onCreate()
        LocationRuntimeDiagnostics.serviceRunning.set(true)
        JarvisNotifications.createChannels(this)
        startForegroundSafely(0)
        registerBatteryReceiver()
        startPendingObserver()
        applyCadenceFromSettings()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                JarvisSettings.setLocationEnabled(this, false)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SYNC -> {
                container.locationSyncCoordinator.requestImmediateSync(this)
                return START_STICKY
            }
        }

        if (!hasLocationPermission() || JarvisSettings.nativeToken(this).isEmpty()) {
            LocationRuntimeDiagnostics.logWarn("Location service stopping: permission or token missing")
            stopSelf()
            return START_NOT_STICKY
        }

        applyCadenceFromSettings()
        startEngineOnMain()
        return START_STICKY
    }

    private fun applyCadenceFromSettings() {
        val cadence = JarvisSettings.locationCadence(this)
        container.adaptiveLocationPolicy.setCadenceMode(cadence)
    }

    private fun startEngineOnMain() {
        mainHandler.post {
            if (!hasLocationPermission()) return@post
            val policy = container.adaptiveLocationPolicy
            policy.setLowBattery(isLowBattery())
            val config = policy.currentConfig()
            if (engineStarted) {
                container.locationEngine.stop()
                engineStarted = false
            }
            container.locationEngine.start(config) { location ->
                scope.launch { handleLocation(location) }
            }
            engineStarted = true
        }
    }

    private suspend fun handleLocation(location: CapturedLocation) {
        val policy = container.adaptiveLocationPolicy
        policy.setLowBattery(isLowBattery())

        persistMeta(LocationConstants.META_LAST_CALLBACK_AT, location.capturedAt)

        val validator = LocationValidator(policy.validationConfig())
        val validation = validator.validate(location)
        if (!validation.valid) {
            LocationRuntimeDiagnostics.onRejected(validation.errorMessage ?: "invalid")
            persistMeta(LocationConstants.META_LAST_REJECT_REASON, System.currentTimeMillis(), validation.errorMessage)
            container.pendingLocationStore.enqueue(
                location = location,
                syncState = PendingLocationSyncState.INVALID,
                errorCode = validation.errorCode,
                errorMessage = validation.errorMessage,
            )
            return
        }

        LocationRuntimeDiagnostics.onAccepted()
        persistMeta(LocationConstants.META_LAST_CAPTURE_AT, location.capturedAt)

        val recent = container.pendingLocationStore.getRecentForDedup()
        val heartbeatMs = policy.heartbeatIntervalMs()
        if (!container.locationDeduplicator.shouldKeep(location, recent, heartbeatMs)) {
            LocationRuntimeDiagnostics.onRejected("duplicate")
            persistMeta(LocationConstants.META_LAST_REJECT_REASON, System.currentTimeMillis(), "duplicate")
            return
        }

        val enqueueResult = container.pendingLocationStore.enqueue(location)
        LocationRuntimeDiagnostics.onInserted(enqueueResult.clientPointId)
        persistMeta(LocationConstants.META_LAST_INSERT_AT, System.currentTimeMillis())

        policy.onLocationRetained(location)
        container.locationSyncCoordinator.recordCaptured()
        container.locationSyncCoordinator.requestImmediateSync(this@JarvisLocationService)
    }

    private suspend fun persistMeta(key: String, atMillis: Long, detail: String? = null) {
        container.database.syncMetadataDao().upsert(
            SyncMetadataEntity(
                key = key,
                lastSuccessAtMillis = atMillis,
                lastError = detail,
            ),
        )
    }

    private fun startPendingObserver() {
        observeJob?.cancel()
        val dao = container.database.pendingLocationDao()
        observeJob = scope.launch {
            combine(
                dao.observeCountByState(PendingLocationSyncState.PENDING),
                dao.observeCountByState(PendingLocationSyncState.FAILED_RETRYABLE),
            ) { pending, retryable -> pending + retryable }
                .collect { count -> startForegroundSafely(count) }
        }
    }

    private fun startForegroundSafely(pendingCount: Int) {
        val notification = JarvisNotifications.locationForeground(this, pendingCount)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        } catch (ex: SecurityException) {
            LocationRuntimeDiagnostics.logWarn("startForeground denied: ${ex.message}")
        }
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
        LocationRuntimeDiagnostics.serviceRunning.set(false)
        observeJob?.cancel()
        batteryReceiver?.let { unregisterReceiver(it) }
        mainHandler.post {
            container.locationEngine.stop()
            engineStarted = false
        }
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "fr.jarvis.companion.location.STOP"
        const val ACTION_SYNC = "fr.jarvis.companion.location.SYNC"
        private const val NOTIFICATION_ID = 4101
    }
}
