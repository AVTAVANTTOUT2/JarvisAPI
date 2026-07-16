package fr.jarvis.companion.voice

import fr.jarvis.companion.core.ui.components.OrbState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceStateTest {
    @Test
    fun defaultUiState_mapsToIdleVisualState() {
        val visual = VoiceUiState(isPaired = true, connectionOk = true).toVisualState()

        assertEquals(OrbState.Idle, visual.orbState)
        assertEquals("Prêt", visual.phaseTitle)
        assertEquals("Tape pour parler en push-to-talk.", visual.phaseHint)
        assertEquals("PTT", visual.primaryButtonLabel)
        assertTrue(visual.isPrimaryButtonEnabled)
        assertFalse(visual.showCancelButton)
        assertFalse(visual.showStopPlaybackButton)
        assertFalse(visual.showRetryButton)
        assertFalse(visual.showOfflineBanner)
        assertEquals("Connecté", visual.connectionLabel)
    }

    @Test
    fun recordingPhase_mapsToRecordingOrbAndCancelAction() {
        val visual = VoiceUiState(
            phase = VoicePhase.Recording,
            isPaired = true,
            connectionOk = true,
            amplitude = 0.72f,
        ).toVisualState()

        assertEquals(OrbState.Recording, visual.orbState)
        assertEquals("Écoute active", visual.phaseTitle)
        assertEquals("Appuie sur STOP pour envoyer ou Annuler pour recommencer.", visual.phaseHint)
        assertEquals("STOP", visual.primaryButtonLabel)
        assertTrue(visual.isPrimaryButtonEnabled)
        assertTrue(visual.showCancelButton)
        assertFalse(visual.showStopPlaybackButton)
    }

    @Test
    fun sendingAndProcessing_mapToProcessingOrbAndDisablePrimaryAction() {
        val sending = VoiceUiState(
            phase = VoicePhase.Sending,
            isPaired = true,
            connectionOk = true,
        ).toVisualState()
        val processing = VoiceUiState(
            phase = VoicePhase.Processing,
            isPaired = true,
            connectionOk = true,
        ).toVisualState()

        assertEquals(OrbState.Processing, sending.orbState)
        assertEquals(OrbState.Processing, processing.orbState)
        assertFalse(sending.isPrimaryButtonEnabled)
        assertFalse(processing.isPrimaryButtonEnabled)
        assertEquals("Traitement en cours", processing.phaseTitle)
    }

    @Test
    fun playingPhase_mapsToSpeakingOrbAndStopButton() {
        val visual = VoiceUiState(
            phase = VoicePhase.Playing,
            isPaired = true,
            connectionOk = true,
        ).toVisualState()

        assertEquals(OrbState.Speaking, visual.orbState)
        assertEquals("Lecture en cours", visual.phaseTitle)
        assertTrue(visual.showStopPlaybackButton)
        assertFalse(visual.showCancelButton)
    }

    @Test
    fun errorPhase_mapsToRetryVisualState() {
        val visual = VoiceUiState(
            phase = VoicePhase.Error,
            isPaired = true,
            connectionOk = true,
            errorMessage = "Microphone indisponible",
        ).toVisualState()

        assertEquals(OrbState.Error, visual.orbState)
        assertEquals("Erreur vocale", visual.phaseTitle)
        assertTrue(visual.showRetryButton)
        assertTrue(visual.isPrimaryButtonEnabled)
    }

    @Test
    fun disconnectedState_mapsToOfflineVisualState() {
        val visual = VoiceUiState(
            phase = VoicePhase.Idle,
            isPaired = false,
            connectionOk = false,
        ).toVisualState()

        assertEquals(OrbState.Offline, visual.orbState)
        assertEquals("Connexion requise", visual.phaseTitle)
        assertEquals("Appairage requis", visual.connectionLabel)
        assertTrue(visual.showOfflineBanner)
        assertTrue(visual.showRetryButton)
        assertFalse(visual.isPrimaryButtonEnabled)
    }

    @Test
    fun activeRecording_keepsRecordingOrbEvenIfConnectionDrops() {
        val visual = VoiceUiState(
            phase = VoicePhase.Recording,
            isPaired = true,
            connectionOk = false,
        ).toVisualState()

        assertEquals(OrbState.Recording, visual.orbState)
        assertTrue(visual.showOfflineBanner)
    }

    @Test
    fun formatRecordingDuration_formatsMinutesAndSeconds() {
        assertEquals("0:00", formatRecordingDuration(0L))
        assertEquals("0:05", formatRecordingDuration(5_400L))
        assertEquals("1:02", formatRecordingDuration(62_000L))
        assertEquals("12:05", formatRecordingDuration(725_000L))
    }
}
