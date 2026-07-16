package fr.jarvis.companion.feature.tasks

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedTaskEntity
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisEmptyState
import fr.jarvis.companion.core.ui.components.JarvisFutureAction
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisPriorityDot
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.format.JarvisTimeFormat
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/** Tâches ouvertes synchronisées depuis le Mac — filtres simples, zéro tableau. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(
    viewModel: TasksViewModel,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()

    PullToRefreshBox(
        isRefreshing = state.isRefreshing,
        onRefresh = viewModel::refresh,
        modifier = modifier.fillMaxSize(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = JarvisSpacing.lg),
            verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
        ) {
            SectionHeader(
                "Tâches",
                "Synchronisées depuis le Mac",
                modifier = Modifier.padding(top = JarvisSpacing.lg),
            )

            if (state.connectivity == ConnectivityState.Offline) {
                JarvisOfflineBanner()
            }
            state.error?.let { ErrorCallout(it) }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
            ) {
                TaskFilter.entries.forEach { f ->
                    FilterChip(
                        selected = state.filter == f,
                        onClick = { viewModel.setFilter(f) },
                        label = { Text(f.label) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = JarvisColors.Cyan.copy(alpha = 0.15f),
                            selectedLabelColor = JarvisColors.Cyan,
                            labelColor = JarvisColors.TextSecondary,
                        ),
                    )
                }
            }

            if (state.tasks.isEmpty()) {
                JarvisEmptyState(
                    icon = Icons.Outlined.Checklist,
                    title = "Rien en attente",
                    description = when (state.filter) {
                        TaskFilter.ALL -> "Aucune tâche ouverte. Tout est sous contrôle, Monsieur."
                        TaskFilter.HIGH -> "Aucune tâche en haute priorité."
                        TaskFilter.OVERDUE -> "Aucune tâche en retard."
                    },
                )
            } else {
                LazyColumn(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
                    contentPadding = PaddingValues(bottom = JarvisSpacing.xxl),
                ) {
                    items(state.tasks, key = { it.serverId }) { task ->
                        TaskCard(task)
                    }
                    if (!JarvisFeatureFlags.TASKS_MUTATIONS) {
                        item {
                            // TODO(JARVIS-FUTURE-TASKS-MUTATIONS): brancher création et
                            // complétion offline-first (modèle pending_chat_operations)
                            // quand les mutations /api/tasks Bearer seront disponibles.
                            JarvisFutureAction(
                                title = "Créer et terminer des tâches",
                                description = "Les modifications depuis le téléphone arrivent dans une prochaine version.",
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TaskCard(task: CachedTaskEntity) {
    JarvisGlassCard(contentPadding = PaddingValues(JarvisSpacing.md)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    task.title,
                    style = MaterialTheme.typography.bodyLarge,
                    color = JarvisColors.TextPrimary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (task.description.isNotBlank()) {
                    Text(
                        task.description,
                        style = MaterialTheme.typography.bodySmall,
                        color = JarvisColors.TextSecondary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm)) {
                    JarvisTimeFormat.dueLabel(task.dueDate)?.let { due ->
                        Text(
                            due,
                            style = MaterialTheme.typography.labelSmall,
                            color = if (due.startsWith("en retard")) JarvisColors.Red
                            else JarvisColors.TextSecondary,
                        )
                    }
                    task.category?.takeIf { it.isNotBlank() }?.let {
                        Text(
                            it,
                            style = MaterialTheme.typography.labelSmall,
                            color = JarvisColors.TextTertiary,
                        )
                    }
                }
            }
            JarvisPriorityDot(task.priority)
        }
    }
}
