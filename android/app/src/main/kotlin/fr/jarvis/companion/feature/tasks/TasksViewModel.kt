package fr.jarvis.companion.feature.tasks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedTaskEntity
import java.time.LocalDate
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class TaskFilter(val label: String) {
    ALL("Toutes"),
    HIGH("Haute priorité"),
    OVERDUE("En retard"),
}

data class TasksUiState(
    val tasks: List<CachedTaskEntity> = emptyList(),
    val filter: TaskFilter = TaskFilter.ALL,
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val isRefreshing: Boolean = false,
    val error: String? = null,
)

/** Tâches ouvertes — cache Room `cached_tasks` (lecture seule, sync serveur). */
class TasksViewModel(
    private val container: AppContainer,
) : ViewModel() {
    private val filter = MutableStateFlow(TaskFilter.ALL)

    private val _uiState = MutableStateFlow(TasksUiState())
    val uiState: StateFlow<TasksUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            combine(
                container.database.cachedTaskDao().observeOpen(),
                container.connectivityObserver.state,
                filter,
            ) { tasks, conn, f ->
                Triple(tasks, conn, f)
            }.collect { (tasks, conn, f) ->
                _uiState.update { current ->
                    current.copy(
                        tasks = applyFilter(tasks, f),
                        filter = f,
                        connectivity = conn,
                    )
                }
            }
        }
        refresh()
    }

    fun setFilter(value: TaskFilter) {
        filter.value = value
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isRefreshing = true, error = null) }
            val result = container.syncManager.refreshHome()
            _uiState.update { current ->
                current.copy(
                    isRefreshing = false,
                    error = result.partialErrors.find { it.startsWith("Tâches") },
                )
            }
        }
    }

    private fun applyFilter(tasks: List<CachedTaskEntity>, f: TaskFilter): List<CachedTaskEntity> =
        when (f) {
            TaskFilter.ALL -> tasks
            TaskFilter.HIGH -> tasks.filter {
                it.priority.equals("high", ignoreCase = true) ||
                    it.priority.equals("urgent", ignoreCase = true)
            }
            TaskFilter.OVERDUE -> tasks.filter { isOverdue(it.dueDate) }
        }

    private fun isOverdue(dueDate: String?): Boolean {
        if (dueDate.isNullOrBlank()) return false
        return try {
            LocalDate.parse(dueDate.take(10)).isBefore(LocalDate.now())
        } catch (_: Exception) {
            false
        }
    }
}

class TasksViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(TasksViewModel::class.java)) {
            return TasksViewModel(container) as T
        }
        throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}
