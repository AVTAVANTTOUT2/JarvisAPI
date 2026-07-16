package fr.jarvis.companion.voice

import fr.jarvis.companion.core.ui.components.OrbState

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
    /** Durée d'enregistrement écoulée (ms), 0 hors phase Recording. */
    val recordingElapsedMs: Long = 0L,
    val turns: List<VoiceTurn> = emptyList(),
    val errorMessage: String? = null,
    val statusLine: String = "Prêt",
)

/** Format chronomètre vocal `m:ss` — fonction pure pour tests. */
fun formatRecordingDuration(elapsedMs: Long): String {
    val totalSeconds = (elapsedMs.coerceAtLeast(0L) / 1000L).toInt()
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%d:%02d".format(minutes, seconds)
}

/** État visuel dérivé et déterministe pour l'écran voix Compose. */
data class VoiceVisualState(
    val orbState: OrbState,
    val orbStateDescription: String,
    val phaseTitle: String,
    val phaseHint: String,
    val connectionLabel: String,
    val primaryButtonLabel: String,
    val primaryButtonContentDescription: String,
    val isPrimaryButtonEnabled: Boolean,
    val showCancelButton: Boolean,
    val showStopPlaybackButton: Boolean,
    val showRetryButton: Boolean,
    val showOfflineBanner: Boolean,
)

/**
 * Projection pure du state métier vers l'état visuel.
 * Elle est utilisée telle quelle par l'UI et testée unitairement.
 */
fun VoiceUiState.toVisualState(): VoiceVisualState {
    val isOffline = !isPaired || !connectionOk
    val orb = when {
        phase == VoicePhase.Error -> OrbState.Error
        phase == VoicePhase.Recording -> OrbState.Recording
        phase == VoicePhase.Sending || phase == VoicePhase.Processing -> OrbState.Processing
        phase == VoicePhase.Playing -> OrbState.Speaking
        isOffline -> OrbState.Offline
        else -> OrbState.Idle
    }

    val phaseTitle = when {
        phase == VoicePhase.Recording -> "Écoute active"
        phase == VoicePhase.Sending -> "Envoi en cours"
        phase == VoicePhase.Processing -> "Traitement en cours"
        phase == VoicePhase.Playing -> "Lecture en cours"
        phase == VoicePhase.Error -> "Erreur vocale"
        isOffline -> "Connexion requise"
        else -> "Prêt"
    }

    val phaseHint = when {
        phase == VoicePhase.Recording ->
            "Appuie sur STOP pour envoyer ou Annuler pour recommencer."
        phase == VoicePhase.Sending ->
            "Transcription en cours sur le Mac."
        phase == VoicePhase.Processing ->
            "JARVIS prépare une réponse vocale."
        phase == VoicePhase.Playing ->
            "Appuie sur Arrêter pour interrompre la lecture."
        phase == VoicePhase.Error ->
            "Réessaie après avoir vérifié la connexion et le micro."
        !isPaired ->
            "Associe ce téléphone à JARVIS pour activer la voix."
        !connectionOk ->
            "Le Mac n'est pas joignable pour le moment."
        else ->
            "Tape pour parler en push-to-talk."
    }

    val canToggleMic = isPaired &&
        connectionOk &&
        (phase == VoicePhase.Idle || phase == VoicePhase.Error || phase == VoicePhase.Recording)

    val connectionLabel = when {
        !isPaired -> "Appairage requis"
        !connectionOk -> "Hors ligne"
        phase == VoicePhase.Error -> "Erreur"
        else -> "Connecté"
    }

    val primaryButtonLabel = if (phase == VoicePhase.Recording) "STOP" else "PTT"
    val primaryButtonContentDescription = if (phase == VoicePhase.Recording) {
        "Stopper et envoyer l'enregistrement"
    } else {
        "Démarrer l'enregistrement push to talk"
    }

    return VoiceVisualState(
        orbState = orb,
        orbStateDescription = "$phaseTitle. $phaseHint",
        phaseTitle = phaseTitle,
        phaseHint = phaseHint,
        connectionLabel = connectionLabel,
        primaryButtonLabel = primaryButtonLabel,
        primaryButtonContentDescription = primaryButtonContentDescription,
        isPrimaryButtonEnabled = canToggleMic,
        showCancelButton = phase == VoicePhase.Recording,
        showStopPlaybackButton = phase == VoicePhase.Playing,
        showRetryButton = phase == VoicePhase.Error || isOffline,
        showOfflineBanner = isOffline,
    )
}
