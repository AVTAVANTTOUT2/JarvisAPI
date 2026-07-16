package fr.jarvis.companion.feature.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.NetworkStatusBadge
import fr.jarvis.companion.core.ui.components.SectionHeader

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    viewModel: HomeViewModel,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text("Accueil") },
                actions = {
                    if (state.isRefreshing) {
                        CircularProgressIndicator(modifier = Modifier.padding(end = 16.dp))
                    } else {
                        IconButton(onClick = viewModel::refresh) {
                            Icon(Icons.Default.Refresh, contentDescription = "Actualiser")
                        }
                    }
                },
            )
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
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SectionHeader(title = "JARVIS", subtitle = "Tableau de bord")
                    NetworkStatusBadge(
                        state = state.connectivity,
                        cachedHint = state.showCachedBanner,
                    )
                }

                if (state.showCachedBanner) {
                    Text(
                        "Données en cache — dernière synchro peut être obsolète.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.tertiary,
                    )
                }

                state.lastSyncMessage?.let { message ->
                    Text(message, style = MaterialTheme.typography.bodySmall)
                }

                BriefingSection(
                    briefing = state.briefing,
                    error = state.briefingError,
                    offline = state.showCachedBanner,
                )
                TasksSection(
                    tasks = state.tasks,
                    error = state.tasksError,
                )
                EventsSection(
                    events = state.events,
                    error = state.eventsError,
                )
                NotificationsSection(
                    notifications = state.notifications,
                    error = state.notificationsError,
                )
            }
        }
    }
}

@Composable
private fun BriefingSection(
    briefing: fr.jarvis.companion.core.database.CachedBriefingEntity?,
    error: String?,
    offline: Boolean,
) {
    JarvisCard(title = "Briefing") {
        when {
            error != null -> ErrorCallout(error)
            briefing != null -> {
                Text(
                    briefing.kind.replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(briefing.content, style = MaterialTheme.typography.bodyMedium)
                if (offline) {
                    Spacer(Modifier.height(4.dp))
                    Text("Source : cache local", style = MaterialTheme.typography.labelSmall)
                }
            }
            else -> Text("Aucun briefing disponible. Tirez pour actualiser quand le Mac est joignable.")
        }
    }
}

@Composable
private fun TasksSection(
    tasks: List<fr.jarvis.companion.core.database.CachedTaskEntity>,
    error: String?,
) {
    JarvisCard(title = "Tâches ouvertes") {
        if (error != null) ErrorCallout(error)
        if (tasks.isEmpty() && error == null) {
            Text("Aucune tâche en attente.")
        } else {
            tasks.take(5).forEach { task ->
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(task.title, fontWeight = FontWeight.Medium)
                    Text(
                        "${task.priority} · ${task.status}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            if (tasks.size > 5) {
                Text("+ ${tasks.size - 5} autres", style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun EventsSection(
    events: List<fr.jarvis.companion.core.database.CachedEventEntity>,
    error: String?,
) {
    JarvisCard(title = "Agenda (7 jours)") {
        if (error != null) ErrorCallout(error)
        if (events.isEmpty() && error == null) {
            Text("Aucun événement sur la période.")
        } else {
            events.take(5).forEach { event ->
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(event.title, fontWeight = FontWeight.Medium)
                    Text(event.startIso, style = MaterialTheme.typography.bodySmall)
                    event.location?.let {
                        Text(it, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun NotificationsSection(
    notifications: List<fr.jarvis.companion.core.database.CachedNotificationEntity>,
    error: String?,
) {
    JarvisCard(title = "Notifications") {
        if (error != null) ErrorCallout(error)
        if (notifications.isEmpty() && error == null) {
            Text("Aucune notification non lue.")
        } else {
            notifications.take(5).forEach { notif ->
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(notif.title, fontWeight = FontWeight.Medium)
                    Text(notif.content, style = MaterialTheme.typography.bodySmall, maxLines = 2)
                }
            }
        }
    }
}
