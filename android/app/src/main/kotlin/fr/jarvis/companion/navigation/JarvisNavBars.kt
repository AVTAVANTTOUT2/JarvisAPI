package fr.jarvis.companion.navigation

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.ui.theme.JarvisColors

private data class NavEntry(
    val route: String,
    val label: String,
    val icon: ImageVector,
    val iconSelected: ImageVector,
)

private val navEntries = listOf(
    NavEntry(JarvisDestination.HOME, "Accueil", Icons.Outlined.Home, Icons.Filled.Home),
    NavEntry(JarvisDestination.CHAT, "Chat", Icons.AutoMirrored.Outlined.Chat, Icons.AutoMirrored.Filled.Chat),
    NavEntry(JarvisDestination.VOICE, "Voix", Icons.Filled.Mic, Icons.Filled.Mic),
    NavEntry(JarvisDestination.CALENDAR, "Agenda", Icons.Outlined.CalendarMonth, Icons.Filled.CalendarMonth),
    NavEntry(JarvisDestination.MORE, "Plus", Icons.Outlined.MoreHoriz, Icons.Filled.MoreHoriz),
)

/**
 * Barre de navigation basse JARVIS — translucide, hairline supérieure,
 * bouton Voix central en orbe miniature (dégradé cyan → bleu).
 */
@Composable
fun JarvisBottomBar(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(JarvisColors.Bg.copy(alpha = 0.92f)),
    ) {
        Box(
            Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(JarvisColors.Hairline),
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .height(64.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            navEntries.forEach { entry ->
                if (entry.route == JarvisDestination.VOICE) {
                    VoiceOrbButton(onClick = { onNavigate(entry.route) })
                } else {
                    NavBarItem(
                        entry = entry,
                        selected = currentRoute == entry.route,
                        onClick = { onNavigate(entry.route) },
                    )
                }
            }
        }
    }
}

@Composable
private fun NavBarItem(
    entry: NavEntry,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val tint = if (selected) JarvisColors.Cyan else JarvisColors.TextSecondary
    Column(
        modifier = Modifier
            .width(64.dp)
            .height(64.dp)
            .selectable(
                selected = selected,
                onClick = onClick,
                role = Role.Tab,
            ),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            if (selected) entry.iconSelected else entry.icon,
            contentDescription = entry.label,
            tint = tint,
            modifier = Modifier.size(23.dp),
        )
        Text(
            entry.label,
            style = MaterialTheme.typography.labelSmall,
            color = tint,
        )
        Box(
            Modifier
                .padding(top = 2.dp)
                .size(3.dp)
                .background(
                    if (selected) JarvisColors.Cyan else Color.Transparent,
                    CircleShape,
                ),
        )
    }
}

/** Bouton Voix — orbe miniature au centre de la barre. */
@Composable
private fun VoiceOrbButton(onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .size(48.dp)
            .background(
                Brush.linearGradient(listOf(JarvisColors.Cyan, JarvisColors.Blue)),
                CircleShape,
            )
            .background(
                Brush.radialGradient(
                    colors = listOf(Color.White.copy(alpha = 0.25f), Color.Transparent),
                    center = androidx.compose.ui.geometry.Offset(30f, 24f),
                    radius = 60f,
                ),
                CircleShape,
            )
            .selectable(selected = false, onClick = onClick, role = Role.Button),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            Icons.Filled.Mic,
            contentDescription = "Voix — parler à JARVIS",
            tint = JarvisColors.Bg,
            modifier = Modifier.size(22.dp),
        )
    }
}

/** Rail de navigation (écrans ≥ 840 dp) — même langage que la barre basse. */
@Composable
fun JarvisNavRail(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(modifier = modifier.fillMaxHeight()) {
        Column(
            modifier = Modifier
                .width(84.dp)
                .fillMaxHeight()
                .background(JarvisColors.Bg.copy(alpha = 0.92f))
                .padding(vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(18.dp, Alignment.CenterVertically),
        ) {
            navEntries.forEach { entry ->
                if (entry.route == JarvisDestination.VOICE) {
                    VoiceOrbButton(onClick = { onNavigate(entry.route) })
                } else {
                    NavBarItem(
                        entry = entry,
                        selected = currentRoute == entry.route,
                        onClick = { onNavigate(entry.route) },
                    )
                }
            }
        }
        Box(
            Modifier
                .width(1.dp)
                .fillMaxHeight()
                .background(JarvisColors.Hairline),
        )
    }
}
