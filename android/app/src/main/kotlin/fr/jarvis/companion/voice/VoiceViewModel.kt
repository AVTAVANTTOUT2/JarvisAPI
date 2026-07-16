package fr.jarvis.companion.voice

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.data.JarvisRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class VoiceViewModel(application: Application) : AndroidViewModel(application) {
    private val voiceRepository = VoiceRepository(application)
    private val statusRepository = JarvisRepository(application)
    private val recorder = VoiceRecorder(application)
    private val player = VoicePlayer(application)

    private val _state = MutableStateFlow(VoiceUiState())
    val state: StateFlow<VoiceUiState> = _state.asStateFlow()

    private var activeFile: File? = null
    private var chatConversationLocalId: Long? = null

    init {
        refreshConnection()
    }

    fun initFromIntent(conversationServerId: Long?, conversationLocalId: Long?) {
        chatConversationLocalId = conversationLocalId
        if (conversationServerId != null) {
            _state.update { it.copy(conversationId = conversationServerId) }
            persistConversationId(conversationServerId)
        }
    }

    fun refreshConnection() {
        viewModelScope.launch {
            val server = voiceRepository.serverUrl()
            val paired = voiceRepository.hasToken()
            _state.update {
                it.copy(
                    serverUrl = server,
                    isPaired = paired,
                    connectionOk = false,
                    statusLine = when {
                        !voiceRepository.isHttpsConfigured() -> "Serveur HTTPS requis"
                        !paired -> "Appairage requis"
                        else -> "Vérification…"
                    },
                )
            }
            if (!paired || !voiceRepository.isHttpsConfigured()) return@launch
            val result = statusRepository.validateNativeToken()
            if (result.ok) {
                _state.update {
                    it.copy(connectionOk = true, statusLine = "Prêt", errorMessage = null)
                }
            } else {
                _state.update {
                    it.copy(
                        connectionOk = false,
                        statusLine = "Serveur injoignable",
                        errorMessage = result.error,
                    )
                }
            }
        }
    }

    /** Tap 1 = démarrer, tap 2 = envoyer (évite de relâcher trop tôt en maintien). */
    fun toggleRecording() {
        when (_state.value.phase) {
            VoicePhase.Idle, VoicePhase.Error -> startRecording()
            VoicePhase.Recording -> stopRecordingAndSend()
            else -> Unit
        }
    }

    fun startRecording() {
        if (_state.value.phase != VoicePhase.Idle && _state.value.phase != VoicePhase.Error) return
        if (!_state.value.isPaired) {
            _state.update { it.copy(phase = VoicePhase.Error, errorMessage = "Appairage requis") }
            return
        }
        runCatching {
            activeFile = recorder.start()
            _state.update {
                it.copy(
                    phase = VoicePhase.Recording,
                    errorMessage = null,
                    statusLine = "Écoute… tapez STOP pour envoyer",
                )
            }
        }.onFailure { err ->
            _state.update {
                it.copy(
                    phase = VoicePhase.Error,
                    errorMessage = err.message ?: "Microphone indisponible",
                    statusLine = "Erreur",
                )
            }
        }
    }

    fun cancelRecording() {
        recorder.cancel()
        activeFile?.delete()
        activeFile = null
        _state.update { it.copy(phase = VoicePhase.Idle, statusLine = "Prêt") }
    }

    fun stopRecordingAndSend() {
        if (_state.value.phase != VoicePhase.Recording) return
        val file = recorder.stop() ?: activeFile
        activeFile = null
        if (file == null || !file.exists() || file.length() < MIN_FILE_BYTES) {
            file?.delete()
            recorder.cancel()
            _state.update {
                it.copy(
                    phase = VoicePhase.Error,
                    errorMessage = "Enregistrement trop court — reparlez un peu plus longtemps",
                    statusLine = "Erreur",
                )
            }
            return
        }
        viewModelScope.launch { submitTurn(file) }
    }

    fun stopPlayback() {
        player.stop()
        if (_state.value.phase == VoicePhase.Playing) {
            _state.update { it.copy(phase = VoicePhase.Idle, statusLine = "Prêt") }
        }
    }

    override fun onCleared() {
        recorder.cancel()
        player.stop()
        activeFile?.delete()
        super.onCleared()
    }

    private suspend fun submitTurn(file: File) {
        _state.update { it.copy(phase = VoicePhase.Sending, statusLine = "Envoi…", errorMessage = null) }
        val convId = _state.value.conversationId
        val result = withContext(Dispatchers.IO) {
            voiceRepository.sendVoiceTurn(file, convId)
        }
        file.delete()
        when (result) {
            is VoiceApiResult.Success -> handleSuccess(result.body)
            is VoiceApiResult.Failure -> _state.update {
                it.copy(
                    phase = VoicePhase.Error,
                    statusLine = "Erreur",
                    errorMessage = result.message,
                )
            }
        }
    }

    private fun handleSuccess(body: VoiceTurnResponse) {
        val turn = VoiceTurn(
            userText = body.transcript,
            assistantText = body.responseText,
            audioBase64 = body.audioBase64,
            audioMimeType = body.audioMimeType,
        )
        _state.update {
            it.copy(
                conversationId = body.conversationId,
                turns = it.turns + turn,
                phase = VoicePhase.Processing,
                statusLine = "Réponse reçue",
                errorMessage = body.ttsError,
            )
        }
        persistConversationId(body.conversationId)
        refreshChatMessages(body.conversationId)
        val audio = body.audioBase64
        if (!audio.isNullOrBlank()) {
            playResponse(audio, body.audioMimeType)
        } else {
            _state.update { it.copy(phase = VoicePhase.Idle, statusLine = "Prêt") }
        }
    }

    private fun playResponse(base64: String, mime: String?) {
        _state.update { it.copy(phase = VoicePhase.Playing, statusLine = "Lecture…") }
        player.playBase64(
            base64 = base64,
            mimeType = mime,
            onComplete = {
                _state.update { it.copy(phase = VoicePhase.Idle, statusLine = "Prêt") }
            },
            onError = { message ->
                _state.update {
                    it.copy(
                        phase = VoicePhase.Idle,
                        statusLine = "Prêt",
                        errorMessage = message,
                    )
                }
            },
        )
    }

    private fun persistConversationId(id: Long) {
        JarvisSettings.preferences(getApplication()).edit()
            .putLong(JarvisSettings.PREF_VOICE_CONVERSATION, id)
            .apply()
    }

    fun restoreConversationId() {
        val id = JarvisSettings.preferences(getApplication())
            .getLong(JarvisSettings.PREF_VOICE_CONVERSATION, -1L)
        if (id > 0) {
            _state.update { it.copy(conversationId = id) }
        }
    }

    private fun refreshChatMessages(conversationServerId: Long) {
        val app = getApplication<Application>()
        val localId = chatConversationLocalId
        if (localId != null && localId > 0) {
            viewModelScope.launch(Dispatchers.IO) {
                runCatching {
                    val container = (app as fr.jarvis.companion.app.JarvisApplication).container
                    container.chatRepository.refreshMessages(localId)
                }
            }
        }
    }

    companion object {
        private const val MIN_FILE_BYTES = 1000L
    }
}
