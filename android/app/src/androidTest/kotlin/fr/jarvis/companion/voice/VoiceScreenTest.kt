package fr.jarvis.companion.voice

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import fr.jarvis.companion.ui.theme.JarvisTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VoiceScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun pttButton_invokesMicCallback() {
        var micTapped = false
        setVoiceContent(
            state = VoiceUiState(
                phase = VoicePhase.Idle,
                isPaired = true,
                connectionOk = true,
            ),
            onMicTap = { micTapped = true },
        )

        composeRule.onNodeWithText("PTT").assertExists().performClick()

        assertTrue(micTapped)
    }

    @Test
    fun recordingState_showsStopCancelAndOrbTalkBackDescription() {
        var cancelTapped = false
        val state = VoiceUiState(
            phase = VoicePhase.Recording,
            isPaired = true,
            connectionOk = true,
        )

        setVoiceContent(
            state = state,
            onMicCancel = { cancelTapped = true },
        )

        composeRule.onNodeWithText("STOP").assertExists()
        composeRule.onNodeWithText("Annuler").assertExists().performClick()
        composeRule.onNodeWithText("Arrêter").assertDoesNotExist()
        composeRule
            .onNodeWithContentDescription(state.toVisualState().orbStateDescription)
            .assertExists()

        assertTrue(cancelTapped)
    }

    @Test
    fun playingState_showsStopAndRealTurnTexts() {
        var stopTapped = false
        val state = VoiceUiState(
            phase = VoicePhase.Playing,
            isPaired = true,
            connectionOk = true,
            turns = listOf(
                VoiceTurn(
                    userText = "Lance mon briefing",
                    assistantText = "Briefing prêt, Monsieur.",
                ),
            ),
        )
        setVoiceContent(
            state = state,
            onStopPlayback = { stopTapped = true },
        )

        composeRule.onNodeWithText("Lance mon briefing").assertExists()
        composeRule.onNodeWithText("Briefing prêt, Monsieur.").assertExists()
        composeRule.onNodeWithText("Arrêter").assertExists().performClick()

        assertTrue(stopTapped)
    }

    @Test
    fun offlineState_showsRetryAndContinuousPlaceholder() {
        var retryTapped = false
        setVoiceContent(
            state = VoiceUiState(
                phase = VoicePhase.Idle,
                isPaired = false,
                connectionOk = false,
            ),
            onRefresh = { retryTapped = true },
        )

        composeRule.onNodeWithText("Réessayer").assertExists().performClick()
        composeRule.onNodeWithText("Conversation continue").assertExists()
        composeRule.onNodeWithText("Bientôt").assertExists()

        assertTrue(retryTapped)
    }

    private fun setVoiceContent(
        state: VoiceUiState,
        onRefresh: () -> Unit = {},
        onMicTap: () -> Unit = {},
        onMicCancel: () -> Unit = {},
        onStopPlayback: () -> Unit = {},
    ) {
        composeRule.setContent {
            JarvisTheme {
                VoiceScreen(
                    state = state,
                    onRefresh = onRefresh,
                    onMicTap = onMicTap,
                    onMicCancel = onMicCancel,
                    onStopPlayback = onStopPlayback,
                )
            }
        }
    }
}
