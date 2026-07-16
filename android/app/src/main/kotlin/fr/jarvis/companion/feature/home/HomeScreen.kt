package fr.jarvis.companion.feature.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.TextUnitType
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.database.CachedBriefingEntity
import fr.jarvis.companion.core.database.CachedEventEntity
import fr.jarvis.companion.core.database.CachedNotificationEntity
import fr.jarvis.companion.core.database.CachedTaskEntity
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisFutureAction
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisPriorityDot
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.JarvisSectionLabel
import fr.jarvis.companion.core.ui.components.NetworkStatusBadge
import fr.jarvis.companion.core.ui.format.JarvisTimeFormat
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/**
 * Accueil JARVIS : salutation horodatée, présence du Mac, briefing héro,
 * actions rapides, journée (agenda), tâches prioritaires, notifications.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    viewModel: HomeViewModel,
    onOpenChat: () -> Unit,
    onOpenVoice: () -> Unit,
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
                .verticalScroll(rememberScrollState())
                .padding(JarvisSpacing.lg),
            verticalArrangement = Arrangement.spacedBy(JarvisSpacing.lg),
        ) {
            HomeHeader(state = state)

            if (state.showCachedBanner) {
                JarvisOfflineBanner()
            }

            QuickActions(
                onOpenVoice = onOpenVoice,
                onOpenChat = onOpenChat,
                onSync = viewModel::refresh,
                syncing = state.isRefreshing,
            )

            BriefingCard(
                briefing = state.briefing,
                error = state.briefingError,
                offline = state.showCachedBanner,
            )

            JarvisSectionLabel("Votre journée")
            TodayTimeline(events = state.events, error = state.eventsError)

            JarvisSectionLabel("Tâches prioritaires")
            TasksCard(tasks = state.tasks, error = state.tasksError)

            JarvisSectionLabel("Notifications")
            NotificationsCard(
                notifications = state.notifications,
                error = state.notificationsError,
            )

            if (!JarvisFeatureFlags.DASHBOARD_CUSTOM) {
                // TODO(JARVIS-FUTURE-DASHBOARD-CUSTOM): brancher la personnalisation
                // des cartes d'accueil (ordre, masquage) sans refonte visuelle.
                JarvisFutureAction(
                    title = "Personnaliser le tableau de bord",
                    description = "Réorganisation des cartes — bientôt disponible.",
                )
            }

            state.lastSyncMessage?.let { message ->
                Text(
                    message,
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisColors.TextTertiary,
                )
            }
        }
    }
}

@Composable
private fun HomeHeader(state: HomeUiState) {
    Column(verticalArrangement = Arrangement.spacedBy(JarvisSpacing.xs)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "JARVIS",
                style = MaterialTheme.typography.labelLarge,
                color = JarvisColors.Cyan,
                letterSpacing = TextUnit(3f, TextUnitType.Sp),
            )
            NetworkStatusBadge(
                state = state.connectivity,
                cachedHint = state.showCachedBanner,
            )
        }
        Text(
            JarvisTimeFormat.greeting(),
            style = MaterialTheme.typography.displaySmall,
            color = JarvisColors.TextPrimary,
        )
        Text(
            JarvisTimeFormat.dayLabel(java.time.LocalDate.now())
                .replaceFirstChar { it.uppercase() },
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisColors.TextSecondary,
        )
    }
}

@Composable
private fun QuickActions(
    onOpenVoice: () -> Unit,
    onOpenChat: () -> Unit,
    onSync: () -> Unit,
    syncing: Boolean,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
    ) {
        JarvisSecondaryButton(
            text = "Parler",
            onClick = onOpenVoice,
            icon = Icons.Outlined.Mic,
            modifier = Modifier.weight(1f),
        )
        JarvisSecondaryButton(
            text = "Écrire",
            onClick = onOpenChat,
            icon = Icons.AutoMirrored.Outlined.Chat,
            modifier = Modifier.weight(1f),
        )
        JarvisSecondaryButton(
            text = if (syncing) "Sync…" else "Sync",
            onClick = onSync,
            enabled = !syncing,
            icon = Icons.Outlined.Sync,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun BriefingCard(
    briefing: CachedBriefingEntity?,
    error: String?,
    offline: Boolean,
) {
    JarvisGlassCard(variant = GlassVariant.Accent) {
        Text(
            "Briefing",
            style = MaterialTheme.typography.labelMedium,
            color = JarvisColors.Cyan,
        )
        when {
            error != null -> ErrorCallout(error)
            briefing != null -> {
                Text(
                    briefing.content,
                    style = MaterialTheme.typography.bodyLarge,
                    color = JarvisColors.TextPrimary,
                )
                if (offline) {
                    Text(
                        "Source : cache local",
                        style = MaterialTheme.typography.labelSmall,
                        color = JarvisColors.TextTertiary,
                    )
                }
            }
            else -> Text(
                "Aucun briefing disponible. Tirez pour actualiser quand le Mac est joignable.",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisColors.TextSecondary,
            )
        }
    }
}

@Composable
private fun TodayTimeline(
    events: List<CachedEventEntity>,
    error: String?,
) {
    JarvisGlassCard {
        if (error != null) ErrorCallout(error)
        val upcoming = events.take(4)
        if (upcoming.isEmpty() && error == null) {
            Text(
                "Rien à l'agenda sur la période. Journée dégagée, Monsieur.",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisColors.TextSecondary,
            )
        } else {
            upcoming.forEach { event ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = JarvisSpacing.xs),
                    horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
                ) {
                    Text(
                        JarvisTimeFormat.timeOrRaw(event.startIso),
                        style = MaterialTheme.typography.titleSmall.copy(
                            fontFeatureSettings = "tnum",
                        ),
                        color = JarvisColors.Cyan,
                    )
                    Column(Modifier.weight(1f)) {
                        Text(
                            event.title,
                            style = MaterialTheme.typography.bodyLarge,
                            color = JarvisColors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        event.location?.takeIf { it.isNotBlank() }?.let {
                            Text(
                                it,
                                style = MaterialTheme.typography.bodySmall,
                                color = JarvisColors.TextSecondary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }
            if (events.size > 4) {
                Text(
                    "+ ${events.size - 4} autres — voir Agenda",
                    style = MaterialTheme.typography.labelSmall,
                    color = JarvisColors.TextTertiary,
                )
            }
        }
    }
}

@Composable
private fun TasksCard(
    tasks: List<CachedTaskEntity>,
    error: String?,
) {
    JarvisGlassCard {
        if (error != null) ErrorCallout(error)
        if (tasks.isEmpty() && error == null) {
            Text(
                "Aucune tâche en attente.",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisColors.TextSecondary,
            )
        } else {
            tasks.take(4).forEach { task ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = JarvisSpacing.xs),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            task.title,
                            style = MaterialTheme.typography.bodyLarge,
                            color = JarvisColors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        JarvisTimeFormat.dueLabel(task.dueDate)?.let { due ->
                            Text(
                                due,
                                style = MaterialTheme.typography.bodySmall,
                                color = if (due.startsWith("en retard")) JarvisColors.Red
                                else JarvisColors.TextSecondary,
                            )
                        }
                    }
                    JarvisPriorityDot(task.priority)
                }
            }
            if (tasks.size > 4) {
                Text(
                    "+ ${tasks.size - 4} autres — voir Tâches",
                    style = MaterialTheme.typography.labelSmall,
                    color = JarvisColors.TextTertiary,
                )
            }
        }
    }
}

@Composable
private fun NotificationsCard(
    notifications: List<CachedNotificationEntity>,
    error: String?,
) {
    JarvisGlassCard {
        if (error != null) ErrorCallout(error)
        if (notifications.isEmpty() && error == null) {
            Text(
                "Rien à signaler.",
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisColors.TextSecondary,
            )
        } else {
            notifications.take(4).forEach { notif ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = JarvisSpacing.xs),
                    horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            notif.title,
                            style = MaterialTheme.typography.bodyLarge,
                            color = JarvisColors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            notif.content,
                            style = MaterialTheme.typography.bodySmall,
                            color = JarvisColors.TextSecondary,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    JarvisPriorityDot(notif.priority)
                }
            }
        }
    }
}
