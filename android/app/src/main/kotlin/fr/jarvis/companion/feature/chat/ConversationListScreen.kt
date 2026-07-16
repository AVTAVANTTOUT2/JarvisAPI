package fr.jarvis.companion.feature.chat

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Archive
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.ChatConversationEntity
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.NetworkStatusBadge
import fr.jarvis.companion.core.ui.components.SectionHeader

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
        topBar = {
            TopAppBar(title = { Text("Conversations") })
        },
        floatingActionButton = {
            FloatingActionButton(onClick = { viewModel.createConversation(onOpenChat) }) {
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
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                NetworkStatusBadge(
                    state = state.connectivity,
                    cachedHint = state.connectivity == ConnectivityState.Offline,
                )
                OutlinedTextField(
                    value = state.searchQuery,
                    onValueChange = viewModel::setSearchQuery,
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("Rechercher…") },
                    singleLine = true,
                )
                state.error?.let { ErrorCallout(it) }

                if (state.groups.isEmpty() && !state.isRefreshing) {
                    Text(
                        "Aucune conversation. Appuyez sur + pour commencer.",
                        modifier = Modifier.padding(24.dp),
                        style = MaterialTheme.typography.bodyLarge,
                    )
                } else {
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        state.groups.forEach { group ->
                            item(key = "header-${group.label}") {
                                SectionHeader(title = group.label, modifier = Modifier.padding(top = 8.dp))
                            }
                            items(group.items, key = { it.localId }) { conv ->
                                ConversationRow(
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
                    Text("Supprimer")
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
private fun ConversationRow(
    conversation: ChatConversationEntity,
    onClick: () -> Unit,
    onRename: () -> Unit,
    onPin: () -> Unit,
    onArchive: () -> Unit,
    onDelete: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                conversation.title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            conversation.lastMessagePreview?.let { preview ->
                Text(
                    preview,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        IconButton(onClick = { menuExpanded = true }) {
            Text("…")
        }
        DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
            DropdownMenuItem(
                text = { Text("Renommer") },
                onClick = { menuExpanded = false; onRename() },
                leadingIcon = { Icon(Icons.Default.Edit, null) },
            )
            DropdownMenuItem(
                text = { Text(if (conversation.isPinned) "Désépingler" else "Épingler") },
                onClick = { menuExpanded = false; onPin() },
                leadingIcon = { Icon(Icons.Default.PushPin, null) },
            )
            DropdownMenuItem(
                text = { Text("Archiver") },
                onClick = { menuExpanded = false; onArchive() },
                leadingIcon = { Icon(Icons.Default.Archive, null) },
            )
            DropdownMenuItem(
                text = { Text("Supprimer") },
                onClick = { menuExpanded = false; onDelete() },
                leadingIcon = { Icon(Icons.Default.Delete, null) },
            )
        }
    }
}
