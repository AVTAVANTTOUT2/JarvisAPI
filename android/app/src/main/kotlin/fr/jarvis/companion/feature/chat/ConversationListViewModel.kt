package fr.jarvis.companion.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.sync.ChatSyncWorker
import fr.jarvis.companion.data.chat.ConversationGrouping
import fr.jarvis.companion.data.chat.GroupedConversations
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ConversationListUiState(
    val groups: List<GroupedConversations> = emptyList(),
    val searchQuery: String = "",
    val isRefreshing: Boolean = false,
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val error: String? = null,
    val showDeleteConfirm: Long? = null,
    val renameTarget: ChatConversationEntity? = null,
    val renameText: String = "",
)

class ConversationListViewModel(
    private val container: AppContainer,
) : ViewModel() {
    private val conversationRepo = container.conversationRepository
    private val connectivity = container.connectivityObserver

    private val _uiState = MutableStateFlow(ConversationListUiState())
    val uiState: StateFlow<ConversationListUiState> = _uiState.asStateFlow()

    private val conversationsFlow = combine(
        _uiState,
        conversationRepo.observeConversations(),
        connectivity.state,
    ) { state, conversations, conn ->
        val filtered = if (state.searchQuery.isBlank()) {
            conversations
        } else {
            val q = state.searchQuery.lowercase()
            conversations.filter {
                it.title.lowercase().contains(q) ||
                    (it.lastMessagePreview?.lowercase()?.contains(q) == true)
            }
        }
        Triple(ConversationGrouping.group(filtered), conn, state.searchQuery)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), Triple(emptyList(), ConnectivityState.Offline, ""))

    init {
        viewModelScope.launch {
            conversationsFlow.collect { (groups, conn, query) ->
                _uiState.update { it.copy(groups = groups, connectivity = conn, searchQuery = query) }
            }
        }
        refresh()
    }

    fun setSearchQuery(query: String) {
        _uiState.update { it.copy(searchQuery = query) }
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isRefreshing = true, error = null) }
            val result = conversationRepo.refreshFromServer()
            ChatSyncWorker.runOnce(container.appContext)
            if (result.unauthorized) {
                _uiState.update { it.copy(isRefreshing = false, error = "Session expirée") }
            } else if (result.error != null) {
                _uiState.update { it.copy(isRefreshing = false, error = result.error) }
            } else {
                _uiState.update { it.copy(isRefreshing = false) }
            }
        }
    }

    fun createConversation(onCreated: (Long) -> Unit) {
        viewModelScope.launch {
            val localId = conversationRepo.createConversation()
            ChatSyncWorker.runOnce(container.appContext)
            onCreated(localId)
        }
    }

    fun requestDelete(localId: Long) {
        _uiState.update { it.copy(showDeleteConfirm = localId) }
    }

    fun dismissDeleteConfirm() {
        _uiState.update { it.copy(showDeleteConfirm = null) }
    }

    fun confirmDelete(localId: Long) {
        viewModelScope.launch {
            conversationRepo.deleteConversation(localId)
            _uiState.update { it.copy(showDeleteConfirm = null) }
            ChatSyncWorker.runOnce(container.appContext)
        }
    }

    fun startRename(conv: ChatConversationEntity) {
        _uiState.update { it.copy(renameTarget = conv, renameText = conv.title) }
    }

    fun setRenameText(text: String) {
        _uiState.update { it.copy(renameText = text) }
    }

    fun confirmRename() {
        val target = _uiState.value.renameTarget ?: return
        val text = _uiState.value.renameText.trim()
        if (text.isBlank()) return
        viewModelScope.launch {
            conversationRepo.renameConversation(target.localId, text)
            _uiState.update { it.copy(renameTarget = null, renameText = "") }
            ChatSyncWorker.runOnce(container.appContext)
        }
    }

    fun dismissRename() {
        _uiState.update { it.copy(renameTarget = null, renameText = "") }
    }

    fun togglePin(localId: Long) {
        viewModelScope.launch {
            conversationRepo.togglePin(localId)
            ChatSyncWorker.runOnce(container.appContext)
        }
    }

    fun archive(localId: Long) {
        viewModelScope.launch {
            conversationRepo.archiveConversation(localId)
            ChatSyncWorker.runOnce(container.appContext)
        }
    }
}

class ConversationListViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(ConversationListViewModel::class.java)) {
            return ConversationListViewModel(container) as T
        }
        throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}
