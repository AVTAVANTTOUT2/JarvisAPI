package fr.jarvis.companion.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing

/**
 * Fond global JARVIS : dégradé bleu nuit → noir, halo cyan très faible en haut
 * et grille pointillée (équivalent du `.bg-grid-pattern` web). Dessin statique,
 * aucune animation.
 */
@Composable
fun JarvisBackground(
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(JarvisColors.BgTop, JarvisColors.Bg),
                ),
            )
            .drawBehind {
                drawRect(
                    brush = Brush.radialGradient(
                        colors = listOf(
                            JarvisColors.Cyan.copy(alpha = 0.05f),
                            Color.Transparent,
                        ),
                        center = Offset(size.width / 2f, -size.height * 0.05f),
                        radius = (size.width * 0.95f).coerceAtLeast(1f),
                    ),
                )
                val step = 24.dp.toPx()
                val dotRadius = 0.8.dp.toPx()
                val dotColor = Color.White.copy(alpha = 0.045f)
                var y = step / 2f
                while (y < size.height) {
                    var x = step / 2f
                    while (x < size.width) {
                        drawCircle(dotColor, radius = dotRadius, center = Offset(x, y))
                        x += step
                    }
                    y += step
                }
            },
        content = content,
    )
}

/** Variantes du panneau verre. */
enum class GlassVariant { Default, Accent, Danger }

private fun glassFill(variant: GlassVariant): Brush = when (variant) {
    GlassVariant.Default -> Brush.verticalGradient(
        listOf(JarvisColors.GlassTop, JarvisColors.GlassBottom),
    )
    GlassVariant.Accent -> Brush.verticalGradient(
        listOf(
            JarvisColors.Cyan.copy(alpha = 0.08f),
            JarvisColors.GlassBottom,
        ),
    )
    GlassVariant.Danger -> Brush.verticalGradient(
        listOf(
            JarvisColors.Red.copy(alpha = 0.08f),
            JarvisColors.GlassBottom,
        ),
    )
}

private fun glassBorder(variant: GlassVariant): Brush = when (variant) {
    GlassVariant.Default -> Brush.verticalGradient(
        listOf(JarvisColors.BorderTop, JarvisColors.BorderBottom),
    )
    GlassVariant.Accent -> Brush.verticalGradient(
        listOf(
            JarvisColors.Cyan.copy(alpha = 0.38f),
            JarvisColors.Cyan.copy(alpha = 0.08f),
        ),
    )
    GlassVariant.Danger -> Brush.verticalGradient(
        listOf(
            JarvisColors.Red.copy(alpha = 0.38f),
            JarvisColors.Red.copy(alpha = 0.08f),
        ),
    )
}

/** Applique le style verre JARVIS (fond translucide + bordure hairline dégradée). */
fun Modifier.jarvisGlass(variant: GlassVariant = GlassVariant.Default): Modifier =
    this
        .clip(androidx.compose.foundation.shape.RoundedCornerShape(20.dp))
        .background(glassFill(variant))
        .border(
            width = 1.dp,
            brush = glassBorder(variant),
            shape = androidx.compose.foundation.shape.RoundedCornerShape(20.dp),
        )

/**
 * Panneau verre JARVIS — remplaçant premium de la Card Material.
 *
 * @param title titre optionnel affiché en tête (style carte du web).
 * @param onClick rend la carte cliquable (ripple standard).
 */
@Composable
fun JarvisGlassCard(
    modifier: Modifier = Modifier,
    variant: GlassVariant = GlassVariant.Default,
    title: String? = null,
    onClick: (() -> Unit)? = null,
    contentPadding: PaddingValues = PaddingValues(JarvisSpacing.lg),
    verticalArrangement: Arrangement.Vertical = Arrangement.spacedBy(JarvisSpacing.sm),
    content: @Composable ColumnScope.() -> Unit,
) {
    val base = modifier
        .fillMaxWidth()
        .jarvisGlass(variant)
    val clickable = if (onClick != null) base.clickable(onClick = onClick) else base
    Column(
        modifier = clickable.padding(contentPadding),
        verticalArrangement = verticalArrangement,
    ) {
        if (title != null) {
            Text(
                title,
                style = MaterialTheme.typography.titleMedium,
                color = JarvisColors.TextPrimary,
            )
        }
        content()
    }
}
