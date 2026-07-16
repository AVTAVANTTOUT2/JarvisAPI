package fr.jarvis.companion.feature.agenda

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedEventEntity
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisEmptyState
import fr.jarvis.companion.core.ui.components.JarvisFutureAction
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisSectionLabel
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.format.JarvisTimeFormat
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing
import java.time.LocalDate

/** Agenda : bandeau 7 jours + timeline Matin / Après-midi / Soir. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AgendaScreen(
    viewModel: AgendaViewModel,
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
            SectionHeader("Agenda", "7 prochains jours")

            if (state.connectivity == ConnectivityState.Offline) {
                JarvisOfflineBanner()
            }
            state.error?.let { ErrorCallout(it) }

            DayStrip(
                days = state.days,
                selected = state.selectedDate,
                counts = state.eventCountByDay,
                onSelect = viewModel::selectDate,
            )

            val hasEvents = state.eventsBySlot.values.any { it.isNotEmpty() }
            if (!hasEvents) {
                JarvisEmptyState(
                    icon = Icons.Outlined.CalendarMonth,
                    title = "Journée dégagée",
                    description = "Aucun événement le " +
                        JarvisTimeFormat.dayLabel(state.selectedDate) +
                        ". Profitez-en, Monsieur.",
                )
            } else {
                DaySlot.entries.forEach { slot ->
                    val events = state.eventsBySlot[slot].orEmpty()
                    if (events.isNotEmpty()) {
                        JarvisSectionLabel(slot.label)
                        events.forEach { event -> EventCard(event) }
                    }
                }
            }

            if (!JarvisFeatureFlags.CALENDAR_CREATE) {
                // TODO(JARVIS-FUTURE-CALENDAR-CREATE): brancher la création rapide
                // d'événement quand POST /api/calendar/* sera exposé au Bearer mobile.
                JarvisFutureAction(
                    title = "Nouvel événement",
                    description = "La création d'événements sera activée dans une prochaine version.",
                )
            }
        }
    }
}

@Composable
private fun DayStrip(
    days: List<LocalDate>,
    selected: LocalDate,
    counts: Map<LocalDate, Int>,
    onSelect: (LocalDate) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
    ) {
        days.forEach { day ->
            val isSelected = day == selected
            Column(
                modifier = Modifier
                    .width(64.dp)
                    .background(
                        if (isSelected) JarvisColors.Cyan.copy(alpha = 0.14f)
                        else JarvisColors.GlassTop,
                        RoundedCornerShape(16.dp),
                    )
                    .selectable(
                        selected = isSelected,
                        onClick = { onSelect(day) },
                        role = Role.Tab,
                    )
                    .padding(vertical = JarvisSpacing.md),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(
                    JarvisTimeFormat.shortDayLabel(day).replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelMedium,
                    color = if (isSelected) JarvisColors.Cyan else JarvisColors.TextSecondary,
                    maxLines = 1,
                )
                val count = counts[day] ?: 0
                Box(
                    Modifier
                        .size(5.dp)
                        .background(
                            if (count > 0) JarvisColors.Cyan else Color.Transparent,
                            CircleShape,
                        ),
                )
            }
        }
    }
}

@Composable
private fun EventCard(event: CachedEventEntity) {
    JarvisGlassCard(contentPadding = androidx.compose.foundation.layout.PaddingValues(JarvisSpacing.md)) {
        Row(horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md)) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    JarvisTimeFormat.timeOrRaw(event.startIso),
                    style = MaterialTheme.typography.titleSmall.copy(fontFeatureSettings = "tnum"),
                    color = JarvisColors.Cyan,
                )
                event.endIso?.let {
                    Text(
                        JarvisTimeFormat.timeOrRaw(it),
                        style = MaterialTheme.typography.labelSmall.copy(fontFeatureSettings = "tnum"),
                        color = JarvisColors.TextTertiary,
                    )
                }
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    event.title,
                    style = MaterialTheme.typography.bodyLarge,
                    color = JarvisColors.TextPrimary,
                    maxLines = 2,
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
                event.notes?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = JarvisColors.TextTertiary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
    }
}
