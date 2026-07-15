package fr.jarvis.companion.voice

/** États UI du tour vocal push-to-talk. */
enum class VoicePhase {
    Idle,
    Recording,
    Sending,
    Processing,
    Playing,
    Error,
}

data class VoiceTurn(
    val userText: String,
    val assistantText: String,
    val audioBase64: String? = null,
    val audioMimeType: String? = null,
)

data class VoiceUiState(
    val phase: VoicePhase = VoicePhase.Idle,
    val serverUrl: String = "",
    val isPaired: Boolean = false,
    val connectionOk: Boolean = false,
    val conversationId: Long? = null,
    val amplitude: Float = 0f,
    val turns: List<VoiceTurn> = emptyList(),
    val errorMessage: String? = null,
    val statusLine: String = "Prêt",
)
