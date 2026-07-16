package fr.jarvis.companion.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/**
 * Tuile métrique : grand chiffre tabulaire + libellé. Le ton colore le chiffre
 * quand la valeur porte un état (attente, erreur…).
 */
@Composable
fun JarvisMetric(
    value: String,
    label: String,
    modifier: Modifier = Modifier,
    tone: StatusTone = StatusTone.Neutral,
) {
    val valueColor = when (tone) {
        StatusTone.Positive -> JarvisColors.Green
        StatusTone.Info -> JarvisColors.Cyan
        StatusTone.Warning -> JarvisColors.Amber
        StatusTone.Danger -> JarvisColors.Red
        StatusTone.Neutral -> JarvisColors.TextPrimary
    }
    Column(
        modifier = modifier
            .jarvisGlass()
            .padding(JarvisSpacing.md),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            value,
            style = MaterialTheme.typography.headlineSmall.copy(
                fontFeatureSettings = "tnum",
                fontWeight = FontWeight.SemiBold,
            ),
            color = valueColor,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = JarvisColors.TextSecondary,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * Ligne standard : icône dans une puce verre, titre, sous-titre, zone de fin
 * libre (badge, switch, chevron…). Cible tactile ≥ 48 dp.
 */
@Composable
fun JarvisListItem(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    iconTint: Color = JarvisColors.TextSecondary,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    val base = if (onClick != null) {
        modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    } else {
        modifier.fillMaxWidth()
    }
    Row(
        modifier = base
            .defaultMinSize(minHeight = 52.dp)
            .padding(vertical = JarvisSpacing.sm),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
    ) {
        if (icon != null) {
            Box(
                modifier = Modifier
                    .size(38.dp)
                    .background(JarvisColors.GlassTop, RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    icon,
                    contentDescription = null,
                    tint = iconTint,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
        Column(Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.bodyLarge,
                color = JarvisColors.TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (!subtitle.isNullOrBlank()) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisColors.TextSecondary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        trailing?.invoke()
    }
}

/** Pastille de priorité avec libellé — utilisée par Tâches et Notifications. */
@Composable
fun JarvisPriorityDot(
    priority: String,
    modifier: Modifier = Modifier,
) {
    val (color, label) = when (priority.lowercase()) {
        "urgent" -> JarvisColors.Red to "Urgent"
        "high", "haute" -> JarvisColors.Red to "Haute"
        "medium", "moyenne" -> JarvisColors.Amber to "Moyenne"
        else -> JarvisColors.TextTertiary to "Basse"
    }
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Box(
            Modifier
                .size(7.dp)
                .background(color, CircleShape),
        )
        Text(label, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

/**
 * Monogramme circulaire (initiale sur dégradé bleu nuit) — avatars des
 * conversations et identités.
 */
@Composable
fun JarvisMonogram(
    text: String,
    modifier: Modifier = Modifier,
    size: androidx.compose.ui.unit.Dp = 40.dp,
) {
    Box(
        modifier = modifier
            .size(size)
            .background(
                Brush.linearGradient(
                    listOf(JarvisColors.UserBubbleTop, JarvisColors.UserBubbleBottom),
                ),
                CircleShape,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text.trim().take(1).uppercase().ifEmpty { "J" },
            style = MaterialTheme.typography.titleMedium.copy(fontSize = (size.value * 0.42f).sp),
            color = JarvisColors.Cyan,
        )
    }
}
