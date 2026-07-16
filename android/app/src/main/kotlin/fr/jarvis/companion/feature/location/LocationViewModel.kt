package fr.jarvis.companion.feature.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.location.AdaptiveLocationMode
import fr.jarvis.companion.core.location.LocationConstants
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

data class LocationUiState(
    val collectionEnabled: Boolean = false,
    val finePermission: Boolean = false,
    val backgroundPermission: Boolean = false,
    val pendingCount: Int = 0,
    val failedCount: Int = 0,
    val invalidCount: Int = 0,
    val lastCaptureAccuracy: String? = null,
    val lastCaptureTime: String? = null,
    val lastSyncRelative: String = "jamais",
    val lastSyncAbsolute: String? = null,
    val frequencyMode: String = "Déplacement",
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val timeline: List<TimelineEntry> = emptyList(),
    val isSyncing: Boolean = false,
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
            dao.observeCountByState(PendingLocationSyncState.FAILED_PERMANENT),
            dao.observeCountByState(PendingLocationSyncState.INVALID),
            container.connectivityObserver.state,
        ) { pending, retryable, failed, invalid, conn ->
            Quint(pending, retryable, failed, invalid, conn)
        },
        metaDao.observe(LocationConstants.META_LAST_SYNC_AT),
        metaDao.observe(LocationConstants.META_LAST_TIMELINE_JSON),
    ) { quint, lastSync, timelineMeta ->
        LocationDataSlice(
            pending = quint.a,
            retryable = quint.b,
            failed = quint.c,
            invalid = quint.d,
            connectivity = quint.e,
            lastSyncAt = lastSync?.lastSuccessAtMillis,
            timelineJson = timelineMeta?.lastError,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), LocationDataSlice())

    init {
        viewModelScope.launch {
            countsFlow.collect { slice ->
                val modeLabel = when (container.adaptiveLocationPolicy.currentMode()) {
                    AdaptiveLocationMode.MOVING -> "Déplacement"
                    AdaptiveLocationMode.STATIONARY -> "Immobile"
                    AdaptiveLocationMode.LOW_BATTERY -> "Batterie faible"
                }
                val lastEngine = container.locationEngine.lastKnown()
                _uiState.update { current ->
                    current.copy(
                        collectionEnabled = JarvisSettings.isLocationEnabled(appContext),
                        finePermission = hasFinePermission(),
                        backgroundPermission = hasBackgroundPermission(),
                        pendingCount = slice.pending + slice.retryable,
                        failedCount = slice.failed,
                        invalidCount = slice.invalid,
                        lastCaptureAccuracy = lastEngine?.accuracy?.let { "± ${it.toInt()} m" },
                        lastCaptureTime = lastEngine?.capturedAt?.let { formatTime(it) },
                        lastSyncRelative = formatRelative(slice.lastSyncAt),
                        lastSyncAbsolute = slice.lastSyncAt?.let { formatTime(it) },
                        frequencyMode = modeLabel,
                        connectivity = slice.connectivity,
                        timeline = parseTimeline(slice.timelineJson),
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

private data class Quint<A, B, C, D, E>(
    val a: A,
    val b: B,
    val c: C,
    val d: D,
    val e: E,
)

private data class LocationDataSlice(
    val pending: Int = 0,
    val retryable: Int = 0,
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
