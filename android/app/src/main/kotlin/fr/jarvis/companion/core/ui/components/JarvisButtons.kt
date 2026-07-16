package fr.jarvis.companion.core.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.ui.theme.JarvisColors

/**
 * Bouton principal JARVIS — capsule dégradée cyan → bleu, texte sombre.
 * `loading` remplace le contenu par un indicateur sans changer la taille.
 */
@Composable
fun JarvisPrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
    icon: ImageVector? = null,
) {
    val shape = RoundedCornerShape(999.dp)
    val gradient = if (enabled && !loading) {
        Brush.horizontalGradient(listOf(JarvisColors.Cyan, JarvisColors.Blue))
    } else {
        Brush.horizontalGradient(
            listOf(
                JarvisColors.Cyan.copy(alpha = 0.25f),
                JarvisColors.Blue.copy(alpha = 0.25f),
            ),
        )
    }
    Surface(
        onClick = onClick,
        modifier = modifier.defaultMinSize(minHeight = 48.dp),
        enabled = enabled && !loading,
        shape = shape,
        color = Color.Transparent,
    ) {
        Row(
            modifier = Modifier
                .background(gradient, shape)
                .defaultMinSize(minHeight = 48.dp)
                .padding(horizontal = 22.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = JarvisColors.Bg,
                )
            } else if (icon != null) {
                Icon(
                    icon,
                    contentDescription = null,
                    tint = JarvisColors.Bg,
                    modifier = Modifier.size(18.dp),
                )
            }
            Text(
                text,
                style = MaterialTheme.typography.labelLarge,
                color = if (enabled && !loading) JarvisColors.Bg
                else JarvisColors.Bg.copy(alpha = 0.7f),
            )
        }
    }
}

/** Bouton secondaire — contour hairline sur verre, texte clair. */
@Composable
fun JarvisSecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    icon: ImageVector? = null,
) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.defaultMinSize(minHeight = 48.dp),
        enabled = enabled,
        shape = RoundedCornerShape(999.dp),
        border = BorderStroke(1.dp, JarvisColors.BorderTop),
        colors = ButtonDefaults.outlinedButtonColors(
            contentColor = JarvisColors.TextPrimary,
            disabledContentColor = JarvisColors.TextTertiary,
        ),
    ) {
        if (icon != null) {
            Icon(
                icon,
                contentDescription = null,
                modifier = Modifier
                    .size(18.dp)
                    .padding(end = 2.dp),
            )
        }
        Text(text, style = MaterialTheme.typography.labelLarge)
    }
}

/** Bouton icône sur puce verre — cible tactile 44 dp minimum. */
@Composable
fun JarvisIconButton(
    icon: ImageVector,
    contentDescription: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    tint: Color = JarvisColors.TextPrimary,
) {
    Box(
        modifier = modifier
            .size(44.dp)
            .background(JarvisColors.GlassTop, RoundedCornerShape(14.dp)),
        contentAlignment = Alignment.Center,
    ) {
        IconButton(onClick = onClick, enabled = enabled) {
            Icon(
                icon,
                contentDescription = contentDescription,
                tint = if (enabled) tint else JarvisColors.TextTertiary,
                modifier = Modifier.size(20.dp),
            )
        }
    }
}
