package fr.jarvis.companion.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedBriefingEntity
import fr.jarvis.companion.core.database.CachedEventEntity
import fr.jarvis.companion.core.database.CachedNotificationEntity
import fr.jarvis.companion.core.database.CachedTaskEntity
import fr.jarvis.companion.core.sync.SyncResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class HomeUiState(
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val briefing: CachedBriefingEntity? = null,
    val tasks: List<CachedTaskEntity> = emptyList(),
    val events: List<CachedEventEntity> = emptyList(),
    val notifications: List<CachedNotificationEntity> = emptyList(),
    val isRefreshing: Boolean = false,
    val lastSyncMessage: String? = null,
    val briefingError: String? = null,
    val tasksError: String? = null,
    val eventsError: String? = null,
    val notificationsError: String? = null,
    val hasCache: Boolean = false,
    val showCachedBanner: Boolean = false,
)

class HomeViewModel(
    private val container: AppContainer,
) : ViewModel() {
    private val db = container.database
    private val syncManager = container.syncManager
    private val connectivity = container.connectivityObserver

    private val _uiState = MutableStateFlow(HomeUiState())
    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    private val dataFlow = combine(
        db.cachedBriefingDao().observeLatest(),
        db.cachedTaskDao().observeOpen(),
        db.cachedEventDao().observeUpcoming(),
        db.cachedNotificationDao().observeUnread(),
        connectivity.state,
    ) { briefing, tasks, events, notifications, conn ->
        HomeDataSlice(briefing, tasks, events, notifications, conn)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeDataSlice())

    init {
        viewModelScope.launch {
            dataFlow.collect { slice ->
                val hasCache = slice.briefing != null ||
                    slice.tasks.isNotEmpty() ||
                    slice.events.isNotEmpty() ||
                    slice.notifications.isNotEmpty()
                _uiState.update { current ->
                    current.copy(
                        connectivity = slice.connectivity,
                        briefing = slice.briefing,
                        tasks = slice.tasks,
                        events = slice.events,
                        notifications = slice.notifications,
                        hasCache = hasCache,
                        showCachedBanner = slice.connectivity == ConnectivityState.Offline && hasCache,
                    )
                }
            }
        }
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isRefreshing = true, lastSyncMessage = null) }
            val result = syncManager.refreshHome()
            _uiState.update { current ->
                current.copy(
                    isRefreshing = false,
                    lastSyncMessage = result.message,
                    briefingError = result.partialErrors.find { it.startsWith("Briefing") },
                    tasksError = result.partialErrors.find { it.startsWith("Tâches") },
                    eventsError = result.partialErrors.find { it.startsWith("Agenda") },
                    notificationsError = result.partialErrors.find { it.startsWith("Notifications") },
                )
            }
        }
    }

    private data class HomeDataSlice(
        val briefing: CachedBriefingEntity? = null,
        val tasks: List<CachedTaskEntity> = emptyList(),
        val events: List<CachedEventEntity> = emptyList(),
        val notifications: List<CachedNotificationEntity> = emptyList(),
        val connectivity: ConnectivityState = ConnectivityState.Offline,
    )
}

class HomeViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(HomeViewModel::class.java)) {
            return HomeViewModel(container) as T
        }
        throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}
