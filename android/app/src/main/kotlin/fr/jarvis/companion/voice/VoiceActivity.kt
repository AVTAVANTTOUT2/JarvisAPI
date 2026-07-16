package fr.jarvis.companion.voice

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.HapticFeedbackConstants
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
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
                Scaffold { padding ->
                    VoiceScreen(
                        modifier = Modifier.padding(padding),
                        state = state,
                        onRefresh = viewModel::refreshConnection,
                        onMicTap = { ensureMicThen { viewModel.toggleRecording() } },
                        onMicCancel = viewModel::cancelRecording,
                        onStopPlayback = viewModel::stopPlayback,
                    )
                }
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
private fun VoiceScreen(
    modifier: Modifier = Modifier,
    state: VoiceUiState,
    onRefresh: () -> Unit,
    onMicTap: () -> Unit,
    onMicCancel: () -> Unit,
    onStopPlayback: () -> Unit,
) {
    val view = LocalView.current
    val busy = state.phase != VoicePhase.Idle &&
        state.phase != VoicePhase.Error &&
        state.phase != VoicePhase.Recording

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("JARVIS", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            state.statusLine,
            style = MaterialTheme.typography.bodyMedium,
            color = if (state.connectionOk) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error,
        )
        if (state.errorMessage != null) {
            Text(state.errorMessage, color = MaterialTheme.colorScheme.error)
        }
        if (!state.isPaired || !state.connectionOk) {
            OutlinedButton(onClick = onRefresh, modifier = Modifier.fillMaxWidth()) {
                Text("Réessayer la connexion")
            }
        }

        state.turns.takeLast(6).forEach { turn ->
            TurnCard(label = "Vous", text = turn.userText)
            TurnCard(label = "JARVIS", text = turn.assistantText)
        }

        if (state.phase == VoicePhase.Sending || state.phase == VoicePhase.Processing) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.padding(end = 12.dp))
                Text(
                    if (state.phase == VoicePhase.Sending) "Envoi au Mac…" else "JARVIS réfléchit…",
                )
            }
        }

        Spacer(modifier = Modifier.height(8.dp))

        Box(
            modifier = Modifier.fillMaxWidth(),
            contentAlignment = Alignment.Center,
        ) {
            val micEnabled = state.isPaired && state.connectionOk && !busy
            Button(
                onClick = {
                    view.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                    onMicTap()
                },
                enabled = micEnabled,
                modifier = Modifier
                    .size(96.dp)
                    .semantics { contentDescription = "Bouton microphone tap pour parler" },
                shape = CircleShape,
                colors = ButtonDefaults.buttonColors(
                    containerColor = when (state.phase) {
                        VoicePhase.Recording -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.primary
                    },
                ),
            ) {
                Text(
                    if (state.phase == VoicePhase.Recording) "STOP" else "MIC",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
        Text(
            if (state.phase == VoicePhase.Recording) {
                "Parlez — tapez à nouveau pour envoyer"
            } else {
                "Tapez pour parler"
            },
            modifier = Modifier.align(Alignment.CenterHorizontally),
            style = MaterialTheme.typography.bodySmall,
        )

        if (state.phase == VoicePhase.Recording) {
            OutlinedButton(onClick = onMicCancel, modifier = Modifier.fillMaxWidth()) {
                Text("Annuler")
            }
        }
        if (state.phase == VoicePhase.Playing) {
            Button(onClick = onStopPlayback, modifier = Modifier.fillMaxWidth()) {
                Text("Arrêter la lecture")
            }
        }
    }
}

@Composable
private fun TurnCard(label: String, text: String) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(label, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary)
            Spacer(modifier = Modifier.height(4.dp))
            Text(text, style = MaterialTheme.typography.bodyLarge)
        }
    }
}
