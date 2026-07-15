package fr.jarvis.companion.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceStateTest {
    @Test
    fun defaultUiState_isIdleAndReady() {
        val state = VoiceUiState()
        assertEquals(VoicePhase.Idle, state.phase)
        assertEquals("Prêt", state.statusLine)
        assertTrue(state.turns.isEmpty())
    }

    @Test
    fun recordingPhase_blocksConcurrentSend() {
        val state = VoiceUiState(phase = VoicePhase.Recording)
        assertFalse(state.phase == VoicePhase.Idle)
    }
}
