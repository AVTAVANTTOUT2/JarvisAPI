package fr.jarvis.companion.feature.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.location.CaptureCadenceMode
import fr.jarvis.companion.core.location.LocationConstants
import fr.jarvis.companion.core.location.LocationRuntimeDiagnostics
import fr.jarvis.companion.core.sync.LocationSyncWorker
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.services.JarvisLocationService
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

data class TimelineEntry(
    val timeLabel: String,
    val label: String,
)

data class RuntimeChainCounters(
    val callbacks: Long = 0L,
    val accepted: Long = 0L,
    val rejected: Long = 0L,
    val inserted: Long = 0L,
    val lastHttpStatus: Int = 0,
    val lastBatchAccepted: Int = 0,
    val engineStarted: Boolean = false,
    val gpsEnabled: Boolean = false,
    val networkEnabled: Boolean = false,
    val serviceRunning: Boolean = false,
)

data class ServerDiagnostics(
    val deviceId: String? = null,
    val pointsReceived24h: Int? = null,
    val lastPointReceivedAt: String? = null,
    val error: String? = null,
)

data class LocationUiState(
    val collectionEnabled: Boolean = false,
    val finePermission: Boolean = false,
    val backgroundPermission: Boolean = false,
    val pendingCount: Int = 0,
    val sendingCount: Int = 0,
    val failedCount: Int = 0,
    val invalidCount: Int = 0,
    val userStatus: String = "Inactif",
    val cadenceMode: CaptureCadenceMode = CaptureCadenceMode.LIVE,
    val lastCaptureAccuracy: String? = null,
    val lastCaptureTime: String? = null,
    val lastSyncRelative: String = "jamais",
    val lastSyncAbsolute: String? = null,
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val timeline: List<TimelineEntry> = emptyList(),
    val runtimeCounters: RuntimeChainCounters = RuntimeChainCounters(),
    val serverDiagnostics: ServerDiagnostics? = null,
    val isSyncing: Boolean = false,
    val isFetchingServerDiag: Boolean = false,
    val message: String? = null,
    val showClearPendingConfirm: Boolean = false,
)

class LocationViewModel(
    private val container: AppContainer,
    private val appContext: Context,
) : ViewModel() {
    private val store = container.pendingLocationStore
    private val dao = container.database.pendingLocationDao()
    private val metaDao = container.database.syncMetadataDao()

    private val _uiState = MutableStateFlow(LocationUiState())
    val uiState: StateFlow<LocationUiState> = _uiState.asStateFlow()

    private val countsFlow = combine(
        combine(
            dao.observeCountByState(PendingLocationSyncState.PENDING),
            dao.observeCountByState(PendingLocationSyncState.FAILED_RETRYABLE),
            dao.observeCountByState(PendingLocationSyncState.SENDING),
        ) { pending: Int, retryable: Int, sending: Int ->
            Triple(pending, retryable, sending)
        },
        combine(
            dao.observeCountByState(PendingLocationSyncState.FAILED_PERMANENT),
            dao.observeCountByState(PendingLocationSyncState.INVALID),
            container.connectivityObserver.state,
        ) { failed: Int, invalid: Int, conn: ConnectivityState ->
            Triple(failed, invalid, conn)
        },
        metaDao.observe(LocationConstants.META_LAST_SYNC_AT),
        metaDao.observe(LocationConstants.META_LAST_TIMELINE_JSON),
    ) { pendingTriple, failedTriple, lastSync, timelineMeta ->
        LocationDataSlice(
            pending = pendingTriple.first,
            retryable = pendingTriple.second,
            sending = pendingTriple.third,
            failed = failedTriple.first,
            invalid = failedTriple.second,
            connectivity = failedTriple.third,
            lastSyncAt = lastSync?.lastSuccessAtMillis,
            timelineJson = timelineMeta?.lastError,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), LocationDataSlice())

    init {
        viewModelScope.launch {
            countsFlow.collect { slice ->
                val lastEngine = container.locationEngine.lastKnown()
                val providersDisabled = areProvidersDisabled()
                val unauthorized = JarvisSettings.nativeToken(appContext).isEmpty()
                val userStatus = LocationRuntimeDiagnostics.buildUserStatus(
                    collectionEnabled = JarvisSettings.isLocationEnabled(appContext),
                    finePermission = hasFinePermission(),
                    pendingCount = slice.pending + slice.retryable,
                    sendingCount = slice.sending,
                    unauthorized = unauthorized,
                    providersDisabled = providersDisabled,
                )
                _uiState.update { current ->
                    current.copy(
                        collectionEnabled = JarvisSettings.isLocationEnabled(appContext),
                        finePermission = hasFinePermission(),
                        backgroundPermission = hasBackgroundPermission(),
                        pendingCount = slice.pending + slice.retryable,
                        sendingCount = slice.sending,
                        failedCount = slice.failed,
                        invalidCount = slice.invalid,
                        userStatus = userStatus,
                        cadenceMode = JarvisSettings.locationCadence(appContext),
                        lastCaptureAccuracy = lastEngine?.accuracy?.let { "± ${it.toInt()} m" },
                        lastCaptureTime = lastEngine?.capturedAt?.let { formatTime(it) },
                        lastSyncRelative = formatRelative(slice.lastSyncAt),
                        lastSyncAbsolute = slice.lastSyncAt?.let { formatTime(it) },
                        connectivity = slice.connectivity,
                        timeline = parseTimeline(slice.timelineJson),
                        runtimeCounters = buildRuntimeCounters(),
                    )
                }
            }
        }
    }

    fun toggleCollection(enabled: Boolean, onNeedsPermission: () -> Unit) {
        if (enabled && !hasFinePermission()) {
            onNeedsPermission()
            return
        }
        JarvisSettings.setLocationEnabled(appContext, enabled)
        if (enabled) {
            appContext.startForegroundService(
                android.content.Intent(appContext, JarvisLocationService::class.java),
            )
        } else {
            appContext.stopService(android.content.Intent(appContext, JarvisLocationService::class.java))
        }
        _uiState.update { it.copy(collectionEnabled = enabled) }
    }

    fun setCadence(mode: CaptureCadenceMode) {
        JarvisSettings.setLocationCadence(appContext, mode)
        container.adaptiveLocationPolicy.setCadenceMode(mode)
        _uiState.update { it.copy(cadenceMode = mode, message = "Cadence : ${mode.labelFr()}") }
        if (JarvisSettings.isLocationEnabled(appContext)) {
            appContext.startForegroundService(
                android.content.Intent(appContext, JarvisLocationService::class.java),
            )
        }
    }

    fun syncNow() {
        viewModelScope.launch {
            _uiState.update { it.copy(isSyncing = true, message = null) }
            LocationSyncWorker.enqueueNow(appContext)
            val outcome = container.locationSyncCoordinator.syncOnce()
            _uiState.update {
                it.copy(
                    isSyncing = false,
                    message = when {
                        outcome.syncedCount > 0 -> "${outcome.syncedCount} point(s) synchronisé(s)"
                        outcome.lockNotAcquired -> "Synchronisation déjà en cours"
                        outcome.skippedNoToken -> "Jeton absent — réappairage requis"
                        outcome.unauthorized -> "Session expirée"
                        outcome.error != null -> outcome.error
                        else -> "Rien à synchroniser"
                    },
                    runtimeCounters = buildRuntimeCounters(),
                )
            }
        }
    }

    fun fetchServerDiagnostics() {
        viewModelScope.launch {
            _uiState.update { it.copy(isFetchingServerDiag = true, message = null) }
            val result = container.repository.getLocationDiagnostics()
            _uiState.update {
                it.copy(
                    isFetchingServerDiag = false,
                    serverDiagnostics = if (result.ok && result.body != null) {
                        ServerDiagnostics(
                            deviceId = result.body.device_id,
                            pointsReceived24h = result.body.points_received_24h,
                            lastPointReceivedAt = result.body.last_point_received_at,
                        )
                    } else {
                        ServerDiagnostics(error = result.error.ifBlank { "HTTP ${result.status}" })
                    },
                    message = if (result.ok) {
                        "Diagnostics serveur récupérés"
                    } else {
                        result.error.ifBlank { "Échec diagnostics serveur" }
                    },
                )
            }
        }
    }

    fun requestClearPendingConfirm() {
        _uiState.update { it.copy(showClearPendingConfirm = true) }
    }

    fun dismissClearPendingConfirm() {
        _uiState.update { it.copy(showClearPendingConfirm = false) }
    }

    fun clearPending() {
        viewModelScope.launch {
            store.cancelAllPending()
            _uiState.update { it.copy(showClearPendingConfirm = false, message = "File en attente vidée") }
        }
    }

    fun clearInvalid() {
        viewModelScope.launch {
            store.clearInvalid()
            _uiState.update { it.copy(message = "Points invalides supprimés") }
        }
    }

    private fun buildRuntimeCounters(): RuntimeChainCounters = RuntimeChainCounters(
        callbacks = LocationRuntimeDiagnostics.lastCallbackAt.get(),
        accepted = LocationRuntimeDiagnostics.lastAcceptedAt.get(),
        rejected = LocationRuntimeDiagnostics.lastRejectedAt.get(),
        inserted = LocationRuntimeDiagnostics.lastInsertAt.get(),
        lastHttpStatus = LocationRuntimeDiagnostics.lastHttpStatus.get(),
        lastBatchAccepted = LocationRuntimeDiagnostics.lastBatchAccepted.get(),
        engineStarted = LocationRuntimeDiagnostics.engineStarted.get(),
        gpsEnabled = LocationRuntimeDiagnostics.gpsProviderEnabled.get(),
        networkEnabled = LocationRuntimeDiagnostics.networkProviderEnabled.get(),
        serviceRunning = LocationRuntimeDiagnostics.serviceRunning.get(),
    )

    private fun areProvidersDisabled(): Boolean {
        if (!hasFinePermission()) return false
        val manager = appContext.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        return !manager.isProviderEnabled(LocationManager.GPS_PROVIDER) &&
            !manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
    }

    private fun hasFinePermission(): Boolean =
        appContext.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun hasBackgroundPermission(): Boolean =
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
            appContext.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            hasFinePermission()
        }

    private fun formatRelative(at: Long?): String {
        if (at == null) return "jamais"
        val diff = System.currentTimeMillis() - at
        val minutes = TimeUnit.MILLISECONDS.toMinutes(diff).coerceAtLeast(0)
        return when {
            minutes < 1 -> "à l'instant"
            minutes < 60 -> "il y a $minutes min"
            else -> "il y a ${minutes / 60} h"
        }
    }

    private fun formatTime(epochMs: Long): String {
        val fmt = SimpleDateFormat("HH:mm", Locale.FRANCE)
        return fmt.format(Date(epochMs))
    }

    private fun parseTimeline(json: String?): List<TimelineEntry> {
        if (json.isNullOrBlank()) return emptyList()
        return try {
            val array = JSONArray(json)
            buildList {
                for (i in 0 until array.length()) {
                    val obj = array.getJSONObject(i)
                    val at = obj.optLong("at")
                    val label = obj.optString("label")
                    add(TimelineEntry(formatTime(at), label))
                }
            }.reversed()
        } catch (_: Exception) {
            emptyList()
        }
    }
}

private data class LocationDataSlice(
    val pending: Int = 0,
    val retryable: Int = 0,
    val sending: Int = 0,
    val failed: Int = 0,
    val invalid: Int = 0,
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val lastSyncAt: Long? = null,
    val timelineJson: String? = null,
)

class LocationViewModelFactory(
    private val container: AppContainer,
    private val context: Context,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(LocationViewModel::class.java)) {
            return LocationViewModel(container, context.applicationContext) as T
        }
        throw IllegalArgumentException("ViewModel inconnu : ${modelClass.name}")
    }
}
