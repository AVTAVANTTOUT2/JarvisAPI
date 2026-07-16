package fr.jarvis.companion.feature.more

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.BugReport
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.ContactPhone
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisComingSoonBadge
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

@Composable
fun MoreScreen(
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val tiles = remember { buildMoreMenuTiles() }
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = JarvisSpacing.lg, vertical = JarvisSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
    ) {
        SectionHeader(
            title = "Plus",
            subtitle = "Actions réelles et fonctions futures inertes",
        )
        LazyVerticalGrid(
            columns = GridCells.Adaptive(minSize = 156.dp),
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
        ) {
            items(tiles, key = { it.title }) { tile ->
                val icon = tileIcon(tile)
                JarvisGlassCard(
                    variant = if (tile.isNavigable()) GlassVariant.Accent else GlassVariant.Default,
                    onClick = tile.route?.let { route -> { onNavigate(route) } },
                    modifier = Modifier
                        .semantics { contentDescription = tile.toAccessibilityHint() }
                        .fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
                ) {
                    Icon(
                        imageVector = icon,
                        contentDescription = null,
                        tint = if (tile.isNavigable()) JarvisColors.Cyan else JarvisColors.TextTertiary,
                    )
                    Text(
                        text = tile.title,
                        style = MaterialTheme.typography.titleMedium,
                        color = if (tile.isNavigable()) JarvisColors.TextPrimary else JarvisColors.TextSecondary,
                    )
                    Text(
                        text = tile.subtitle,
                        style = MaterialTheme.typography.bodySmall,
                        color = JarvisColors.TextSecondary,
                    )
                    if (!tile.isNavigable()) {
                        JarvisComingSoonBadge()
                    }
                }
            }
        }
    }
}

private fun tileIcon(tile: MoreTileModel) = when (tile.title) {
    "Tâches" -> Icons.Outlined.Checklist
    "Localisation" -> Icons.Outlined.Place
    "Notifications" -> Icons.Outlined.Notifications
    "Diagnostics" -> Icons.Outlined.BugReport
    "Réglages" -> Icons.Outlined.Settings
    "Réparation" -> Icons.Outlined.Security
    "Mémoire" -> Icons.Outlined.Memory
    "Contacts" -> Icons.Outlined.ContactPhone
    else -> Icons.Outlined.AutoAwesome
}
