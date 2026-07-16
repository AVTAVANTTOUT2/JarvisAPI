package fr.jarvis.companion.voice

import android.Manifest
import android.content.res.Configuration
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.HapticFeedbackConstants
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Cancel
import androidx.compose.material.icons.outlined.GraphicEq
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.StopCircle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisBackground
import fr.jarvis.companion.core.ui.components.JarvisComingSoonCard
import fr.jarvis.companion.core.ui.components.JarvisEmptyState
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisOrb
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.JarvisSectionLabel
import fr.jarvis.companion.core.ui.components.JarvisStatusBadge
import fr.jarvis.companion.core.ui.components.OrbState
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.components.StatusTone
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing
import fr.jarvis.companion.ui.theme.JarvisTheme

/** Conversation vocale native — tap pour parler / tap pour envoyer (aucune WebView). */
class VoiceActivity : ComponentActivity() {
    companion object {
        const val EXTRA_CONVERSATION_ID = "conversation_id"
        const val EXTRA_CONVERSATION_LOCAL_ID = "conversation_local_id"
    }

    private val viewModel: VoiceViewModel by viewModels()

    private val micPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            viewModel.cancelRecording()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val intentConvId = intent.getLongExtra(EXTRA_CONVERSATION_ID, -1L).takeIf { it > 0 }
        val intentLocalId = intent.getLongExtra(EXTRA_CONVERSATION_LOCAL_ID, -1L).takeIf { it > 0 }
        viewModel.initFromIntent(intentConvId, intentLocalId)
        viewModel.restoreConversationId()
        viewModel.refreshConnection()
        setContent {
            JarvisTheme {
                val state by viewModel.state.collectAsState()
                VoiceScreen(
                    state = state,
                    onRefresh = viewModel::refreshConnection,
                    onMicTap = { ensureMicThen { viewModel.toggleRecording() } },
                    onMicCancel = viewModel::cancelRecording,
                    onStopPlayback = viewModel::stopPlayback,
                )
            }
        }
    }

    private fun ensureMicThen(block: () -> Unit) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            block()
        } else {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
}

@Composable
fun VoiceScreen(
    modifier: Modifier = Modifier,
    state: VoiceUiState,
    onRefresh: () -> Unit,
    onMicTap: () -> Unit,
    onMicCancel: () -> Unit,
    onStopPlayback: () -> Unit,
    continuousVoiceEnabled: Boolean = JarvisFeatureFlags.CONTINUOUS_VOICE,
) {
    val view = LocalView.current
    val visual = state.toVisualState()
    val connectionTone = when {
        !state.isPaired -> StatusTone.Warning
        !state.connectionOk -> StatusTone.Danger
        state.phase == VoicePhase.Error -> StatusTone.Danger
        else -> StatusTone.Positive
    }
    val orbSize = rememberVoiceOrbSize()

    JarvisBackground(modifier = modifier) {
        Scaffold(
            containerColor = Color.Transparent,
        ) { scaffoldPadding ->
            // Column + scroll (pas Lazy) : historique court, tout reste dans l'arbre sémantique.
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(scaffoldPadding)
                    .verticalScroll(rememberScrollState())
                    .padding(
                        start = JarvisSpacing.lg,
                        end = JarvisSpacing.lg,
                        top = JarvisSpacing.xl,
                        bottom = JarvisSpacing.xxl,
                    ),
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.lg),
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm)) {
                    SectionHeader(
                        title = "Voix JARVIS",
                        subtitle = "Pipeline push-to-talk natif, sans WebView",
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm)) {
                        JarvisStatusBadge(
                            label = visual.connectionLabel,
                            tone = connectionTone,
                        )
                        if (state.phase == VoicePhase.Recording) {
                            JarvisStatusBadge(label = "Micro actif", tone = StatusTone.Danger)
                        }
                    }
                    Text(
                        state.statusLine,
                        style = MaterialTheme.typography.bodyMedium,
                        color = JarvisColors.TextSecondary,
                    )
                }

                if (visual.showOfflineBanner) {
                    JarvisOfflineBanner(
                        text = if (!state.isPaired) {
                            "Appairage requis — connecte ce téléphone à JARVIS pour activer la voix"
                        } else {
                            "Hors ligne — impossible d'envoyer la voix au Mac pour le moment"
                        },
                    )
                }

                if (!state.errorMessage.isNullOrBlank()) {
                    ErrorCallout(message = state.errorMessage)
                }

                JarvisGlassCard(
                    variant = when (visual.orbState) {
                        OrbState.Error -> GlassVariant.Danger
                        OrbState.Offline -> GlassVariant.Default
                        else -> GlassVariant.Accent
                    },
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth(),
                        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        JarvisOrb(
                            state = visual.orbState,
                            size = orbSize,
                            amplitude = state.amplitude,
                            stateDescription = visual.orbStateDescription,
                        )
                        Text(
                            visual.phaseTitle,
                            style = MaterialTheme.typography.titleLarge,
                            color = JarvisColors.TextPrimary,
                            textAlign = TextAlign.Center,
                        )
                        if (state.phase == VoicePhase.Recording) {
                            Text(
                                formatRecordingDuration(state.recordingElapsedMs),
                                style = MaterialTheme.typography.headlineMedium.copy(
                                    fontFeatureSettings = "tnum",
                                ),
                                color = JarvisColors.Cyan,
                                textAlign = TextAlign.Center,
                                modifier = Modifier.semantics {
                                    contentDescription =
                                        "Durée d'enregistrement ${formatRecordingDuration(state.recordingElapsedMs)}"
                                },
                            )
                        }
                        Text(
                            visual.phaseHint,
                            style = MaterialTheme.typography.bodyMedium,
                            color = JarvisColors.TextSecondary,
                            textAlign = TextAlign.Center,
                        )
                    }
                }

                JarvisPrimaryButton(
                    text = visual.primaryButtonLabel,
                    onClick = {
                        view.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                        onMicTap()
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .semantics {
                            contentDescription = visual.primaryButtonContentDescription
                        },
                    enabled = visual.isPrimaryButtonEnabled,
                )

                if (visual.showCancelButton) {
                    JarvisSecondaryButton(
                        text = "Annuler",
                        onClick = onMicCancel,
                        icon = Icons.Outlined.Cancel,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (visual.showStopPlaybackButton) {
                    JarvisSecondaryButton(
                        text = "Arrêter",
                        onClick = onStopPlayback,
                        icon = Icons.Outlined.StopCircle,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (visual.showRetryButton) {
                    JarvisSecondaryButton(
                        text = "Réessayer",
                        onClick = onRefresh,
                        icon = Icons.Outlined.Refresh,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                if (!continuousVoiceEnabled) {
                    // TODO(JARVIS-FUTURE-VOICE-CONTINUOUS): brancher le mode
                    // conversation continue quand le pipeline VAD/anti-écho Android sera prêt.
                    JarvisComingSoonCard(
                        title = "Conversation continue",
                        description = "Bientôt disponible derrière un service audio dédié.",
                    )
                }

                JarvisSectionLabel(text = "Transcription et réponse")

                if (state.turns.isEmpty()) {
                    JarvisEmptyState(
                        icon = Icons.Outlined.GraphicEq,
                        title = "Aucun échange vocal",
                        description = "Lance un tour PTT pour afficher la transcription et la réponse réelles.",
                    )
                } else {
                    state.turns.takeLast(6).forEach { turn ->
                        VoiceTurnCard(label = "Vous", text = turn.userText, accent = false)
                        VoiceTurnCard(label = "JARVIS", text = turn.assistantText, accent = true)
                    }
                }
            }
        }
    }
}

@Composable
private fun rememberVoiceOrbSize() =
    with(LocalConfiguration.current) {
        val fontScale = LocalDensity.current.fontScale
        val landscape = orientation == Configuration.ORIENTATION_LANDSCAPE
        when {
            landscape && fontScale >= 1.3f -> 152.dp
            landscape -> 172.dp
            fontScale >= 1.3f -> 188.dp
            else -> 220.dp
        }
    }

@Composable
private fun VoiceTurnCard(
    label: String,
    text: String,
    accent: Boolean,
) {
    JarvisGlassCard(
        variant = if (accent) GlassVariant.Accent else GlassVariant.Default,
        modifier = Modifier.heightIn(min = 64.dp),
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = if (accent) JarvisColors.Cyan else JarvisColors.TextSecondary,
        )
        Spacer(modifier = Modifier.height(2.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodyLarge,
            color = JarvisColors.TextPrimary,
        )
    }
}
