package fr.jarvis.companion.feature.agenda

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedEventEntity
import fr.jarvis.companion.core.ui.format.JarvisTimeFormat
import java.time.LocalDate
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Tranche de journée pour la timeline (matin / après-midi / soir). */
enum class DaySlot(val label: String) {
    MORNING("Matin"),
    AFTERNOON("Après-midi"),
    EVENING("Soir"),
}

data class AgendaUiState(
    val selectedDate: LocalDate = LocalDate.now(),
    val days: List<LocalDate> = emptyList(),
    val eventsBySlot: Map<DaySlot, List<CachedEventEntity>> = emptyMap(),
    val eventCountByDay: Map<LocalDate, Int> = emptyMap(),
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val isRefreshing: Boolean = false,
    val error: String? = null,
)

/**
 * Agenda mobile — lit le cache Room `cached_events` (synchronisé par
 * SyncManager, 7 jours glissants) et le regroupe par jour puis par tranche.
 */
class AgendaViewModel(
    private val container: AppContainer,
) : ViewModel() {
    private val selectedDate = MutableStateFlow(LocalDate.now())

    private val _uiState = MutableStateFlow(
        AgendaUiState(days = (0..6L).map { LocalDate.now().plusDays(it) }),
    )
    val uiState: StateFlow<AgendaUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            combine(
                container.database.cachedEventDao().observeUpcoming(),
                container.connectivityObserver.state,
                selectedDate,
            ) { events, conn, date ->
                Triple(events, conn, date)
            }.collect { (events, conn, date) ->
                val byDay = events.groupBy { event ->
                    JarvisTimeFormat.parseIso(event.startIso)?.toLocalDate()
                }
                val dayEvents = byDay[date].orEmpty().sortedBy { it.startIso }
                val slots = dayEvents.groupBy { event ->
                    val hour = JarvisTimeFormat.parseIso(event.startIso)?.hour ?: 12
                    when {
                        hour < 12 -> DaySlot.MORNING
                        hour < 18 -> DaySlot.AFTERNOON
                        else -> DaySlot.EVENING
                    }
                }
                _uiState.update { current ->
                    current.copy(
                        selectedDate = date,
                        eventsBySlot = slots,
                        eventCountByDay = byDay
                            .filterKeys { it != null }
                            .mapKeys { it.key!! }
                            .mapValues { it.value.size },
                        connectivity = conn,
                    )
                }
            }
        }
        refresh()
    }

    fun selectDate(date: LocalDate) {
        selectedDate.value = date
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isRefreshing = true, error = null) }
            val result = container.syncManager.refreshHome()
            _uiState.update { current ->
                current.copy(
                    isRefreshing = false,
                    error = result.partialErrors.find { it.startsWith("Agenda") },
                )
            }
        }
    }
}

class AgendaViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(AgendaViewModel::class.java)) {
            return AgendaViewModel(container) as T
        }
        throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}
