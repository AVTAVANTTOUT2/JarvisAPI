package fr.jarvis.companion.feature.notifications

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedNotificationEntity
import fr.jarvis.companion.core.ui.components.JarvisEmptyState
import fr.jarvis.companion.core.ui.components.JarvisFutureAction
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisPriorityDot
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/**
 * Notifications non lues (cache Room, lecture seule) — les actions marquer-lu
 * attendent l'exposition Bearer mobile des endpoints correspondants.
 */
@Composable
fun NotificationsScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val container = context.appContainer()
    val notifications by container.database.cachedNotificationDao()
        .observeUnread()
        .collectAsState(initial = emptyList())
    val connectivity by container.connectivityObserver.state.collectAsState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = JarvisSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
    ) {
        SectionHeader(
            "Notifications",
            "Non lues, synchronisées depuis le Mac",
            modifier = Modifier.padding(top = JarvisSpacing.lg),
        )

        if (connectivity == ConnectivityState.Offline) {
            JarvisOfflineBanner()
        }

        if (notifications.isEmpty()) {
            JarvisEmptyState(
                icon = Icons.Outlined.NotificationsNone,
                title = "Rien à signaler",
                description = "Aucune notification non lue. Le calme règne, Monsieur.",
            )
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
                contentPadding = PaddingValues(bottom = JarvisSpacing.xxl),
            ) {
                items(notifications, key = { it.serverId }) { notif ->
                    NotificationCard(notif)
                }
                if (!JarvisFeatureFlags.NOTIFICATIONS_ACTIONS) {
                    item {
                        // TODO(JARVIS-FUTURE-NOTIFICATIONS-CENTER): brancher
                        // « marquer lu » quand POST /api/notifications/{id}/read
                        // sera exposé au Bearer mobile.
                        JarvisFutureAction(
                            title = "Marquer comme lu",
                            description = "La gestion des notifications depuis le téléphone arrive dans une prochaine version.",
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun NotificationCard(notif: CachedNotificationEntity) {
    JarvisGlassCard(contentPadding = PaddingValues(JarvisSpacing.md)) {
        Row(horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    notif.title,
                    style = MaterialTheme.typography.bodyLarge,
                    color = JarvisColors.TextPrimary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    notif.content,
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisColors.TextSecondary,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    notif.source,
                    style = MaterialTheme.typography.labelSmall,
                    color = JarvisColors.TextTertiary,
                )
            }
            JarvisPriorityDot(notif.priority)
        }
    }
}
