package fr.jarvis.companion.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.app.AppContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.database.ChatMessageEntity
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.network.WsConnectionState
import fr.jarvis.companion.core.sync.ChatSyncWorker
import fr.jarvis.companion.data.chat.PendingActionState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ChatUiState(
    val conversation: ChatConversationEntity? = null,
    val messages: List<ChatMessageEntity> = emptyList(),
    val composerText: String = "",
    val isSending: Boolean = false,
    val connectivity: ConnectivityState = ConnectivityState.Offline,
    val wsState: WsConnectionState = WsConnectionState.Disconnected,
    val showOfflineBanner: Boolean = false,
    val pendingAction: PendingActionState? = null,
    val error: String? = null,
)

class ChatViewModel(
    private val container: AppContainer,
    private val conversationLocalId: Long,
) : ViewModel() {
    private val chatRepo = container.chatRepository
    private val conversationRepo = container.conversationRepository
    private val connectivity = container.connectivityObserver

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val dataFlow = combine(
        conversationRepo.observeConversation(conversationLocalId),
        chatRepo.observeMessages(conversationLocalId),
        chatRepo.observeDraft(conversationLocalId),
        connectivity.state,
        chatRepo.wsConnectionState,
    ) { conv, messages, draft, conn, ws ->
        ChatDataSlice(
            conversation = conv,
            messages = messages,
            draft = draft?.draftText.orEmpty(),
            connectivity = conn,
            wsState = ws,
            pendingAction = null,
        )
    }.combine(chatRepo.pendingAction) { slice, pending ->
        slice.copy(pendingAction = pending)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), ChatDataSlice())

    init {
        viewModelScope.launch {
            dataFlow.collect { slice ->
                _uiState.update { current ->
                    current.copy(
                        conversation = slice.conversation,
                        messages = slice.messages,
                        composerText = if (current.composerText.isEmpty()) slice.draft else current.composerText,
                        connectivity = slice.connectivity,
                        wsState = slice.wsState,
                        showOfflineBanner = slice.connectivity == ConnectivityState.Offline,
                    )
                }
            }
        }
        viewModelScope.launch {
            chatRepo.pendingAction.collect { pending ->
                _uiState.update { it.copy(pendingAction = pending) }
            }
        }
        viewModelScope.launch {
            val conv = conversationRepo.observeConversation(conversationLocalId)
            conv.collect { entity ->
                chatRepo.openConversation(conversationLocalId, entity?.serverId)
                entity?.serverId?.let { chatRepo.refreshMessages(conversationLocalId) }
            }
        }
    }

    fun onComposerChanged(text: String) {
        _uiState.update { it.copy(composerText = text) }
        viewModelScope.launch { chatRepo.saveDraft(conversationLocalId, text) }
    }

    fun sendMessage() {
        val text = _uiState.value.composerText.trim()
        if (text.isEmpty() || _uiState.value.isSending) return
        viewModelScope.launch {
            _uiState.update { it.copy(isSending = true, error = null) }
            val result = chatRepo.sendMessage(conversationLocalId, text)
            if (result.error != null) {
                _uiState.update { it.copy(isSending = false, error = result.error) }
            } else {
                _uiState.update { it.copy(isSending = false, composerText = "") }
                chatRepo.clearDraft(conversationLocalId)
            }
            if (result.queued) {
                ChatSyncWorker.runOnce(container.appContext)
            }
        }
    }

    fun refreshMessages() {
        viewModelScope.launch { chatRepo.refreshMessages(conversationLocalId) }
    }

    fun confirmAction(confirmed: Boolean) {
        viewModelScope.launch { chatRepo.confirmPendingAction(confirmed) }
    }

    fun dismissError() {
        _uiState.update { it.copy(error = null) }
    }

    override fun onCleared() {
        chatRepo.closeConversation()
        super.onCleared()
    }

    private data class ChatDataSlice(
        val conversation: ChatConversationEntity? = null,
        val messages: List<ChatMessageEntity> = emptyList(),
        val draft: String = "",
        val connectivity: ConnectivityState = ConnectivityState.Offline,
        val wsState: WsConnectionState = WsConnectionState.Disconnected,
        val pendingAction: PendingActionState? = null,
    )
}

class ChatViewModelFactory(
    private val container: AppContainer,
    private val conversationLocalId: Long,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        if (modelClass.isAssignableFrom(ChatViewModel::class.java)) {
            return ChatViewModel(container, conversationLocalId) as T
        }
        throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}

fun ChatMessageEntity.isUser(): Boolean = role == "user"

fun ChatMessageEntity.showStreamingIndicator(): Boolean =
    isStreaming || deliveryState == DeliveryState.STREAMING ||
        deliveryState == DeliveryState.SENDING
