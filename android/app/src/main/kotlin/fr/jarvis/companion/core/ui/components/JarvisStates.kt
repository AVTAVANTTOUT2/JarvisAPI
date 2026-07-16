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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/** État vide élégant : icône dans un halo verre + titre + description. */
@Composable
fun JarvisEmptyState(
    icon: ImageVector,
    title: String,
    description: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = JarvisSpacing.xxl, horizontal = JarvisSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
    ) {
        Box(
            modifier = Modifier
                .size(64.dp)
                .background(
                    Brush.radialGradient(
                        listOf(
                            JarvisColors.Cyan.copy(alpha = 0.14f),
                            Color.Transparent,
                        ),
                    ),
                    CircleShape,
                )
                .background(JarvisColors.GlassTop, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                icon,
                contentDescription = null,
                tint = JarvisColors.TextSecondary,
                modifier = Modifier.size(28.dp),
            )
        }
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = JarvisColors.TextPrimary,
            textAlign = TextAlign.Center,
        )
        Text(
            description,
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisColors.TextSecondary,
            textAlign = TextAlign.Center,
        )
        action?.invoke()
    }
}

/** État d'erreur plein cadre avec action de réessai optionnelle. */
@Composable
fun JarvisErrorState(
    message: String,
    modifier: Modifier = Modifier,
    onRetry: (() -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(JarvisSpacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
    ) {
        ErrorCallout(message)
        if (onRetry != null) {
            TextButton(onClick = onRetry) {
                Text("Réessayer", color = JarvisColors.Cyan)
            }
        }
    }
}

/**
 * Bandeau hors ligne standard — même langage sur tous les écrans.
 * L'information est portée par l'icône ET le texte.
 */
@Composable
fun JarvisOfflineBanner(
    modifier: Modifier = Modifier,
    text: String = "Hors ligne — les données affichées viennent du cache local",
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = JarvisColors.Amber.copy(alpha = 0.09f),
        shape = MaterialTheme.shapes.small,
        border = androidx.compose.foundation.BorderStroke(
            1.dp,
            JarvisColors.Amber.copy(alpha = 0.22f),
        ),
    ) {
        Row(
            modifier = Modifier.padding(
                horizontal = JarvisSpacing.md,
                vertical = 10.dp,
            ),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
        ) {
            Icon(
                Icons.Outlined.CloudOff,
                contentDescription = null,
                tint = JarvisColors.Amber,
                modifier = Modifier.size(16.dp),
            )
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                color = JarvisColors.Amber,
            )
        }
    }
}

/**
 * Carte placeholder d'une fonctionnalité future — inerte, jamais cliquable,
 * jamais de fausse donnée. Voir android/docs/FUTURE_FEATURES.md.
 */
@Composable
fun JarvisComingSoonCard(
    title: String,
    description: String,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
) {
    JarvisGlassCard(modifier = modifier) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
        ) {
            if (icon != null) {
                Icon(
                    icon,
                    contentDescription = null,
                    tint = JarvisColors.TextTertiary,
                    modifier = Modifier.size(22.dp),
                )
            }
            Column(Modifier.weight(1f)) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    color = JarvisColors.TextSecondary,
                )
                Text(
                    description,
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisColors.TextTertiary,
                )
            }
            JarvisComingSoonBadge()
        }
    }
}

/** Ligne d'action future désactivée (réglages, menus). Inerte. */
@Composable
fun JarvisFutureAction(
    title: String,
    description: String,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = JarvisSpacing.sm),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.bodyLarge,
                color = JarvisColors.TextTertiary,
            )
            Text(
                description,
                style = MaterialTheme.typography.bodySmall,
                color = JarvisColors.TextTertiary,
            )
        }
        JarvisComingSoonBadge()
    }
}

/** État plein écran « fonctionnalité non disponible » (flag désactivé). */
@Composable
fun JarvisFeatureDisabledState(
    title: String,
    description: String,
    modifier: Modifier = Modifier,
    icon: ImageVector = Icons.Outlined.CloudOff,
) {
    JarvisEmptyState(
        icon = icon,
        title = title,
        description = description,
        modifier = modifier,
    )
}
