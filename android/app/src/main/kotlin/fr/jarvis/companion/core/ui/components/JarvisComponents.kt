package fr.jarvis.companion.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/** Carte standard — délègue au panneau verre du design system. */
@Composable
fun JarvisCard(
    modifier: Modifier = Modifier,
    title: String? = null,
    content: @Composable () -> Unit,
) {
    JarvisGlassCard(modifier = modifier, title = title) {
        content()
    }
}

/** En-tête d'écran : titre fort + sous-titre discret. */
@Composable
fun SectionHeader(
    title: String,
    subtitle: String? = null,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Text(
            title,
            style = MaterialTheme.typography.headlineSmall,
            color = JarvisColors.TextPrimary,
        )
        if (!subtitle.isNullOrBlank()) {
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = JarvisColors.TextSecondary,
            )
        }
    }
}

/** Libellé de section discret (majuscules espacées, style web). */
@Composable
fun JarvisSectionLabel(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text.uppercase(),
        modifier = modifier.padding(top = JarvisSpacing.xs),
        style = MaterialTheme.typography.labelMedium,
        color = JarvisColors.TextTertiary,
        letterSpacing = androidx.compose.ui.unit.TextUnit(
            1.2f,
            androidx.compose.ui.unit.TextUnitType.Sp,
        ),
    )
}

/** Encart d'erreur inline (verre teinté rouge). */
@Composable
fun ErrorCallout(
    message: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = JarvisColors.Red.copy(alpha = 0.10f),
        shape = RoundedCornerShape(14.dp),
        border = androidx.compose.foundation.BorderStroke(
            1.dp,
            JarvisColors.Red.copy(alpha = 0.25f),
        ),
    ) {
        Text(
            text = message,
            modifier = Modifier.padding(JarvisSpacing.md),
            color = JarvisColors.Red,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

/** Ton sémantique d'un badge de statut. */
enum class StatusTone { Positive, Info, Warning, Danger, Neutral }

private fun toneColor(tone: StatusTone): Color = when (tone) {
    StatusTone.Positive -> JarvisColors.Green
    StatusTone.Info -> JarvisColors.Cyan
    StatusTone.Warning -> JarvisColors.Amber
    StatusTone.Danger -> JarvisColors.Red
    StatusTone.Neutral -> JarvisColors.TextSecondary
}

/**
 * Badge de statut avec point coloré + libellé — l'information passe toujours
 * par le texte, jamais par la couleur seule.
 */
@Composable
fun JarvisStatusBadge(
    label: String,
    tone: StatusTone,
    modifier: Modifier = Modifier,
) {
    val color = toneColor(tone)
    Surface(
        modifier = modifier,
        color = color.copy(alpha = 0.13f),
        shape = RoundedCornerShape(999.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Box(
                Modifier
                    .size(6.dp)
                    .background(color, CircleShape),
            )
            Text(label, style = MaterialTheme.typography.labelMedium, color = color)
        }
    }
}

/** Pastille « Bientôt » des fonctionnalités futures. */
@Composable
fun JarvisComingSoonBadge(modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = JarvisColors.Amber.copy(alpha = 0.12f),
        shape = RoundedCornerShape(999.dp),
    ) {
        Text(
            "Bientôt",
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            style = MaterialTheme.typography.labelSmall,
            color = JarvisColors.Amber,
        )
    }
}

/** Badge d'état réseau — API historique conservée. */
@Composable
fun NetworkStatusBadge(
    state: ConnectivityState,
    modifier: Modifier = Modifier,
    cachedHint: Boolean = false,
) {
    val (label, tone) = when (state) {
        ConnectivityState.Offline ->
            if (cachedHint) "Hors ligne — cache" to StatusTone.Warning
            else "Hors ligne" to StatusTone.Danger
        ConnectivityState.NetworkAvailable -> "Réseau disponible" to StatusTone.Warning
        ConnectivityState.ServerReachable -> "Connecté" to StatusTone.Positive
        ConnectivityState.Unauthorized -> "Session expirée" to StatusTone.Danger
    }
    JarvisStatusBadge(label = label, tone = tone, modifier = modifier)
}
