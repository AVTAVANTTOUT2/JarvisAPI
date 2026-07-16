package fr.jarvis.companion.feature.chat

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import fr.jarvis.companion.core.database.ChatMessageEntity
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.voice.VoiceActivity

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val listState = rememberLazyListState()
    val lifecycleOwner = LocalLifecycleOwner.current

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                viewModel.refreshMessages()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.lastIndex)
        }
    }

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text(state.conversation?.title ?: "Chat") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Retour")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            if (state.showOfflineBanner) {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    color = MaterialTheme.colorScheme.tertiaryContainer,
                ) {
                    Text(
                        "Hors ligne — les messages seront envoyés à la reconnexion",
                        modifier = Modifier.padding(12.dp),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            state.error?.let { ErrorCallout(it, modifier = Modifier.padding(8.dp)) }

            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(state.messages, key = { it.localId }) { message ->
                    MessageBubble(message = message)
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(8.dp),
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                IconButton(
                    onClick = {
                        val intent = Intent(context, VoiceActivity::class.java).apply {
                            state.conversation?.serverId?.let { putExtra(VoiceActivity.EXTRA_CONVERSATION_ID, it) }
                            putExtra(VoiceActivity.EXTRA_CONVERSATION_LOCAL_ID, state.conversation?.localId ?: -1L)
                        }
                        context.startActivity(intent)
                    },
                ) {
                    Icon(Icons.Default.Mic, contentDescription = "Voix")
                }
                OutlinedTextField(
                    value = state.composerText,
                    onValueChange = viewModel::onComposerChanged,
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Message…") },
                    maxLines = 5,
                )
                IconButton(
                    onClick = viewModel::sendMessage,
                    enabled = !state.isSending && state.composerText.isNotBlank(),
                ) {
                    if (state.isSending) {
                        CircularProgressIndicator(modifier = Modifier.padding(4.dp))
                    } else {
                        Icon(Icons.Default.Send, contentDescription = "Envoyer")
                    }
                }
            }
        }
    }

    state.pendingAction?.let { pending ->
        AlertDialog(
            onDismissRequest = { viewModel.confirmAction(false) },
            title = { Text("Confirmation requise") },
            text = {
                Text(pending.message ?: "JARVIS propose une action sensible. Confirmer ?")
            },
            confirmButton = {
                TextButton(onClick = { viewModel.confirmAction(true) }) {
                    Text("Confirmer")
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.confirmAction(false) }) {
                    Text("Refuser")
                }
            },
        )
    }
}

@Composable
private fun MessageBubble(message: ChatMessageEntity) {
    val isUser = message.isUser()
    val alignment = if (isUser) Alignment.CenterEnd else Alignment.CenterStart
    val bg = if (isUser) {
        MaterialTheme.colorScheme.primaryContainer
    } else {
        MaterialTheme.colorScheme.surfaceVariant
    }

    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = alignment) {
        Column(
            modifier = Modifier
                .fillMaxWidth(0.85f)
                .background(bg, RoundedCornerShape(12.dp))
                .padding(12.dp),
        ) {
            if (message.content.isNotBlank()) {
                Text(message.content, style = MaterialTheme.typography.bodyLarge)
            }
            if (message.showStreamingIndicator() && message.content.isBlank()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                    Text("JARVIS répond…", style = MaterialTheme.typography.bodySmall)
                }
            }
            if (message.deliveryState == DeliveryState.FAILED_RETRYABLE) {
                Text(
                    message.errorMessage ?: "Échec d'envoi",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}
