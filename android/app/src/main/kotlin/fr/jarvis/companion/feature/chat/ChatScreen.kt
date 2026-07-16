package fr.jarvis.companion.feature.chat

import android.content.Intent
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.database.ChatMessageEntity
import fr.jarvis.companion.core.database.DeliveryState
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing
import fr.jarvis.companion.ui.theme.rememberReducedMotion
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
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        state.conversation?.title?.ifBlank { "Chat" } ?: "Chat",
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Retour")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = JarvisColors.Bg.copy(alpha = 0.92f),
                    titleContentColor = JarvisColors.TextPrimary,
                    navigationIconContentColor = JarvisColors.TextPrimary,
                ),
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
                JarvisOfflineBanner(
                    modifier = Modifier.padding(
                        horizontal = JarvisSpacing.md,
                        vertical = JarvisSpacing.xs,
                    ),
                    text = "Hors ligne — les messages seront envoyés à la reconnexion",
                )
            }
            state.error?.let {
                ErrorCallout(it, modifier = Modifier.padding(JarvisSpacing.sm))
            }

            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = JarvisSpacing.md),
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
            ) {
                items(state.messages, key = { it.localId }) { message ->
                    MessageBubble(message = message)
                }
            }

            Composer(
                text = state.composerText,
                onTextChange = viewModel::onComposerChanged,
                isSending = state.isSending,
                onSend = viewModel::sendMessage,
                onVoice = {
                    val intent = Intent(context, VoiceActivity::class.java).apply {
                        state.conversation?.serverId?.let {
                            putExtra(VoiceActivity.EXTRA_CONVERSATION_ID, it)
                        }
                        putExtra(
                            VoiceActivity.EXTRA_CONVERSATION_LOCAL_ID,
                            state.conversation?.localId ?: -1L,
                        )
                    }
                    context.startActivity(intent)
                },
            )
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
                    Text("Confirmer", color = JarvisColors.Cyan)
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

    if (isUser) {
        Box(
            modifier = Modifier.fillMaxWidth(),
            contentAlignment = Alignment.CenterEnd,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth(0.82f)
                    .background(
                        Brush.linearGradient(
                            listOf(JarvisColors.UserBubbleTop, JarvisColors.UserBubbleBottom),
                        ),
                        RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp),
                    )
                    .padding(JarvisSpacing.md),
            ) {
                Text(
                    message.content,
                    style = MaterialTheme.typography.bodyLarge,
                    color = JarvisColors.TextPrimary,
                )
                MessageStatus(message)
            }
        }
    } else {
        // Réponse JARVIS : panneau verre pleine largeur, filet cyan à gauche.
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    Brush.verticalGradient(
                        listOf(JarvisColors.GlassTop, JarvisColors.GlassBottom),
                    ),
                    RoundedCornerShape(18.dp),
                )
                .border(
                    1.dp,
                    Brush.verticalGradient(
                        listOf(JarvisColors.BorderTop, JarvisColors.BorderBottom),
                    ),
                    RoundedCornerShape(18.dp),
                ),
        ) {
            Box(
                Modifier
                    .padding(vertical = JarvisSpacing.md)
                    .width(2.dp)
                    .align(Alignment.Top)
                    .background(JarvisColors.Cyan.copy(alpha = 0.6f)),
            )
            Column(
                Modifier
                    .weight(1f)
                    .padding(JarvisSpacing.md),
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.xs),
            ) {
                Text(
                    "JARVIS",
                    style = MaterialTheme.typography.labelSmall,
                    color = JarvisColors.Cyan,
                )
                if (message.content.isNotBlank()) {
                    Text(
                        message.content,
                        style = MaterialTheme.typography.bodyLarge,
                        color = JarvisColors.TextPrimary,
                    )
                }
                if (message.showStreamingIndicator()) {
                    StreamingDots()
                }
                MessageStatus(message)
            }
        }
    }
}

@Composable
private fun MessageStatus(message: ChatMessageEntity) {
    when (message.deliveryState) {
        DeliveryState.FAILED_RETRYABLE -> Text(
            (message.errorMessage ?: "Échec d'envoi") + " — renvoi automatique",
            color = JarvisColors.Amber,
            style = MaterialTheme.typography.labelSmall,
        )
        DeliveryState.FAILED_PERMANENT -> Text(
            message.errorMessage ?: "Échec d'envoi définitif",
            color = JarvisColors.Red,
            style = MaterialTheme.typography.labelSmall,
        )
        DeliveryState.QUEUED, DeliveryState.LOCAL_PENDING -> Text(
            "En attente d'envoi",
            color = JarvisColors.TextTertiary,
            style = MaterialTheme.typography.labelSmall,
        )
        else -> Unit
    }
}

/** Trois points « respirants » pendant le streaming de la réponse. */
@Composable
private fun StreamingDots() {
    val reducedMotion = rememberReducedMotion()
    if (reducedMotion) {
        Text(
            "JARVIS rédige…",
            style = MaterialTheme.typography.labelSmall,
            color = JarvisColors.TextSecondary,
        )
        return
    }
    val transition = rememberInfiniteTransition(label = "streaming")
    val progress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(900, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "dots",
    )
    Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
        repeat(3) { index ->
            val phase = ((progress + index / 3f) % 1f)
            val alpha = 0.25f + 0.75f * (1f - kotlin.math.abs(phase - 0.5f) * 2f)
            Box(
                Modifier
                    .size(7.dp)
                    .background(JarvisColors.Cyan.copy(alpha = alpha), CircleShape),
            )
        }
    }
}

@Composable
private fun Composer(
    text: String,
    onTextChange: (String) -> Unit,
    isSending: Boolean,
    onSend: () -> Unit,
    onVoice: () -> Unit,
) {
    val showSlashHint = JarvisFeatureFlags.SLASH_COMMANDS.not() && text.startsWith("/")
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(JarvisColors.Bg.copy(alpha = 0.92f))
            .navigationBarsPadding()
            .padding(horizontal = JarvisSpacing.md, vertical = JarvisSpacing.sm),
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.xs),
    ) {
        if (showSlashHint) {
            // TODO(JARVIS-FUTURE-SLASH-COMMANDS): surface de suggestions locale
            // (/nouveau /cherche /briefing /tâche) alignée sur le composer web.
            Text(
                "Commandes slash — bientôt disponibles",
                style = MaterialTheme.typography.labelSmall,
                color = JarvisColors.TextTertiary,
            )
        }
        Row(
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.xs),
        ) {
            IconButton(onClick = onVoice) {
                Icon(
                    Icons.Outlined.Mic,
                    contentDescription = "Passer en vocal",
                    tint = JarvisColors.Cyan,
                )
            }
            if (!JarvisFeatureFlags.CHAT_ATTACHMENTS) {
                // TODO(JARVIS-FUTURE-CHAT-ATTACHMENTS): brancher l'upload de pièces
                // jointes quand POST /api/conversations/{id}/upload existera en Bearer.
                IconButton(onClick = {}, enabled = false) {
                    Icon(
                        Icons.Outlined.AttachFile,
                        contentDescription = "Pièces jointes — bientôt disponible",
                        tint = JarvisColors.TextTertiary,
                    )
                }
            }
            OutlinedTextField(
                value = text,
                onValueChange = onTextChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message…", color = JarvisColors.TextTertiary) },
                maxLines = 5,
                shape = RoundedCornerShape(22.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = JarvisColors.Cyan.copy(alpha = 0.5f),
                    unfocusedBorderColor = JarvisColors.Hairline,
                    focusedContainerColor = JarvisColors.GlassBottom,
                    unfocusedContainerColor = JarvisColors.GlassBottom,
                ),
            )
            val sendEnabled = !isSending && text.isNotBlank()
            Box(
                modifier = Modifier
                    .size(46.dp)
                    .background(
                        if (sendEnabled) {
                            Brush.linearGradient(listOf(JarvisColors.Cyan, JarvisColors.Blue))
                        } else {
                            Brush.linearGradient(
                                listOf(
                                    JarvisColors.Cyan.copy(alpha = 0.22f),
                                    JarvisColors.Blue.copy(alpha = 0.22f),
                                ),
                            )
                        },
                        CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                IconButton(onClick = onSend, enabled = sendEnabled) {
                    if (isSending) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = JarvisColors.Bg,
                        )
                    } else {
                        Icon(
                            Icons.AutoMirrored.Filled.Send,
                            contentDescription = "Envoyer",
                            tint = JarvisColors.Bg,
                            modifier = Modifier.size(20.dp),
                        )
                    }
                }
            }
        }
    }
}
