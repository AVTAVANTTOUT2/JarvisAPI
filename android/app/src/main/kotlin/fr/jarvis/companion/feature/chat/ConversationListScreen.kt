package fr.jarvis.companion.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.database.ConversationSyncState
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisEmptyState
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisMonogram
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisSectionLabel
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.format.JarvisTimeFormat
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationListScreen(
    viewModel: ConversationListViewModel,
    onOpenChat: (Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()

    Scaffold(
        modifier = modifier,
        containerColor = Color.Transparent,
        floatingActionButton = {
            FloatingActionButton(
                onClick = { viewModel.createConversation(onOpenChat) },
                containerColor = JarvisColors.Cyan,
                contentColor = JarvisColors.Bg,
            ) {
                Icon(Icons.Default.Add, contentDescription = "Nouvelle conversation")
            }
        },
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = state.isRefreshing,
            onRefresh = viewModel::refresh,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = JarvisSpacing.lg),
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
            ) {
                SectionHeader(
                    "Conversations",
                    modifier = Modifier.padding(top = JarvisSpacing.lg),
                )

                if (state.connectivity == ConnectivityState.Offline) {
                    JarvisOfflineBanner(
                        text = "Hors ligne — les messages partiront à la reconnexion",
                    )
                }

                OutlinedTextField(
                    value = state.searchQuery,
                    onValueChange = viewModel::setSearchQuery,
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = {
                        Text("Rechercher…", color = JarvisColors.TextTertiary)
                    },
                    leadingIcon = {
                        Icon(
                            Icons.Outlined.Search,
                            contentDescription = null,
                            tint = JarvisColors.TextTertiary,
                        )
                    },
                    singleLine = true,
                    shape = MaterialTheme.shapes.small,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = JarvisColors.Cyan.copy(alpha = 0.5f),
                        unfocusedBorderColor = JarvisColors.Hairline,
                        focusedContainerColor = JarvisColors.GlassBottom,
                        unfocusedContainerColor = JarvisColors.GlassBottom,
                    ),
                )
                state.error?.let { ErrorCallout(it) }

                if (state.groups.isEmpty() && !state.isRefreshing) {
                    JarvisEmptyState(
                        icon = Icons.AutoMirrored.Outlined.Chat,
                        title = "Aucune conversation",
                        description = "Appuyez sur + pour écrire à JARVIS, ou utilisez la voix.",
                    )
                } else {
                    LazyColumn(
                        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
                        contentPadding = PaddingValues(bottom = 88.dp),
                    ) {
                        state.groups.forEach { group ->
                            item(key = "header-${group.label}") {
                                JarvisSectionLabel(group.label)
                            }
                            items(group.items, key = { it.localId }) { conv ->
                                ConversationCard(
                                    conversation = conv,
                                    onClick = { onOpenChat(conv.localId) },
                                    onRename = { viewModel.startRename(conv) },
                                    onPin = { viewModel.togglePin(conv.localId) },
                                    onArchive = { viewModel.archive(conv.localId) },
                                    onDelete = { viewModel.requestDelete(conv.localId) },
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    state.showDeleteConfirm?.let { localId ->
        AlertDialog(
            onDismissRequest = viewModel::dismissDeleteConfirm,
            title = { Text("Supprimer la conversation ?") },
            text = { Text("Cette action est irréversible sur le serveur.") },
            confirmButton = {
                TextButton(onClick = { viewModel.confirmDelete(localId) }) {
                    Text("Supprimer", color = JarvisColors.Red)
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissDeleteConfirm) {
                    Text("Annuler")
                }
            },
        )
    }

    state.renameTarget?.let {
        AlertDialog(
            onDismissRequest = viewModel::dismissRename,
            title = { Text("Renommer") },
            text = {
                OutlinedTextField(
                    value = state.renameText,
                    onValueChange = viewModel::setRenameText,
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(onClick = viewModel::confirmRename) { Text("Enregistrer") }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissRename) { Text("Annuler") }
            },
        )
    }
}

@Composable
private fun ConversationCard(
    conversation: ChatConversationEntity,
    onClick: () -> Unit,
    onRename: () -> Unit,
    onPin: () -> Unit,
    onArchive: () -> Unit,
    onDelete: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }

    JarvisGlassCard(
        onClick = onClick,
        contentPadding = PaddingValues(
            start = JarvisSpacing.md,
            end = JarvisSpacing.xs,
            top = JarvisSpacing.md,
            bottom = JarvisSpacing.md,
        ),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
        ) {
            JarvisMonogram(conversation.title.ifBlank { "J" })
            Column(Modifier.weight(1f)) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (conversation.isPinned) {
                        Icon(
                            Icons.Outlined.PushPin,
                            contentDescription = "Épinglée",
                            tint = JarvisColors.Purple,
                            modifier = Modifier.size(13.dp),
                        )
                    }
                    Text(
                        conversation.title.ifBlank { "Nouvelle conversation" },
                        style = MaterialTheme.typography.titleSmall,
                        color = JarvisColors.TextPrimary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    Text(
                        JarvisTimeFormat.relativeFromNow(
                            conversation.lastMessageAtMillis ?: conversation.createdAtMillis,
                        ),
                        style = MaterialTheme.typography.labelSmall,
                        color = JarvisColors.TextTertiary,
                    )
                }
                conversation.lastMessagePreview?.let { preview ->
                    Text(
                        preview,
                        style = MaterialTheme.typography.bodySmall,
                        color = JarvisColors.TextSecondary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (conversation.syncState == ConversationSyncState.PENDING_CREATE ||
                    conversation.pendingDeletion
                ) {
                    Text(
                        if (conversation.pendingDeletion) "Suppression en attente"
                        else "Synchronisation en attente",
                        style = MaterialTheme.typography.labelSmall,
                        color = JarvisColors.Amber,
                    )
                }
            }
            IconButton(onClick = { menuExpanded = true }) {
                Icon(
                    Icons.Outlined.MoreVert,
                    contentDescription = "Options de la conversation",
                    tint = JarvisColors.TextSecondary,
                )
            }
            DropdownMenu(
                expanded = menuExpanded,
                onDismissRequest = { menuExpanded = false },
            ) {
                DropdownMenuItem(
                    text = { Text("Renommer") },
                    onClick = { menuExpanded = false; onRename() },
                    leadingIcon = { Icon(Icons.Outlined.Edit, null) },
                )
                DropdownMenuItem(
                    text = { Text(if (conversation.isPinned) "Désépingler" else "Épingler") },
                    onClick = { menuExpanded = false; onPin() },
                    leadingIcon = { Icon(Icons.Outlined.PushPin, null) },
                )
                DropdownMenuItem(
                    text = { Text("Archiver") },
                    onClick = { menuExpanded = false; onArchive() },
                    leadingIcon = { Icon(Icons.Outlined.Archive, null) },
                )
                DropdownMenuItem(
                    text = { Text("Supprimer", color = JarvisColors.Red) },
                    onClick = { menuExpanded = false; onDelete() },
                    leadingIcon = {
                        Icon(Icons.Outlined.Delete, null, tint = JarvisColors.Red)
                    },
                )
            }
        }
    }
}
