package fr.jarvis.companion.core.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.rememberReducedMotion

/** États visuels de l'orbe JARVIS — chacun encode un état réel du pipeline vocal. */
enum class OrbState {
    /** Au repos, prêt — halo cyan qui respire lentement. */
    Idle,

    /** Enregistrement en cours — anneau rouge doux, rayon lié à l'amplitude micro. */
    Recording,

    /** Envoi / traitement — arc cyan en rotation. */
    Processing,

    /** JARVIS parle — ondes concentriques sortantes. */
    Speaking,

    /** Erreur — teinte rouge statique. */
    Error,

    /** Hors ligne / non appairé — orbe gris éteint. */
    Offline,
}

private fun orbCoreColor(state: OrbState): Color = when (state) {
    OrbState.Recording -> JarvisColors.Red
    OrbState.Error -> JarvisColors.Red
    OrbState.Offline -> JarvisColors.TextTertiary
    else -> JarvisColors.Cyan
}

/**
 * Orbe JARVIS — signature visuelle de l'app, dessinée en Canvas pur
 * (aucune image, aucun blur). Les animations infinies sont coupées si
 * l'utilisateur a réduit les animations système : chaque état reste alors
 * différencié par sa couleur et sa géométrie statique.
 *
 * @param amplitude niveau micro normalisé 0..1 (utilisé en [OrbState.Recording]).
 * @param stateDescription annonce TalkBack de l'état courant.
 */
@Composable
fun JarvisOrb(
    state: OrbState,
    modifier: Modifier = Modifier,
    size: Dp = 200.dp,
    amplitude: Float = 0f,
    stateDescription: String? = null,
) {
    val reducedMotion = rememberReducedMotion()
    val animate = !reducedMotion

    val infinite = rememberInfiniteTransition(label = "orb")

    // Respiration (Idle) — 1 → 1.05 sur 3 s.
    val breath by infinite.animateFloat(
        initialValue = 1f,
        targetValue = if (animate && state == OrbState.Idle) 1.05f else 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(3000, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "breath",
    )

    // Rotation (Processing) — tour complet en 1.4 s.
    val rotation by infinite.animateFloat(
        initialValue = 0f,
        targetValue = if (animate && state == OrbState.Processing) 360f else 0f,
        animationSpec = infiniteRepeatable(
            animation = tween(1400, easing = LinearEasing),
        ),
        label = "rotation",
    )

    // Ondes (Speaking) — progression 0 → 1 en boucle.
    val wave by infinite.animateFloat(
        initialValue = 0f,
        targetValue = if (animate && state == OrbState.Speaking) 1f else 0f,
        animationSpec = infiniteRepeatable(
            animation = tween(1600, easing = LinearEasing),
        ),
        label = "wave",
    )

    // Amplitude micro adoucie (Recording).
    val smoothAmplitude by animateFloatAsState(
        targetValue = amplitude.coerceIn(0f, 1f),
        animationSpec = spring(stiffness = 220f),
        label = "amplitude",
    )

    val core = orbCoreColor(state)

    Canvas(
        modifier = modifier
            .size(size)
            .semantics {
                if (stateDescription != null) contentDescription = stateDescription
            },
    ) {
        val center = Offset(this.size.width / 2f, this.size.height / 2f)
        val baseRadius = this.size.minDimension * 0.30f

        // Halo extérieur.
        val haloScale = when (state) {
            OrbState.Idle -> breath
            OrbState.Recording -> 1f + smoothAmplitude * 0.22f
            else -> 1f
        }
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(core.copy(alpha = 0.20f), Color.Transparent),
                center = center,
                radius = baseRadius * 2.1f * haloScale,
            ),
            radius = baseRadius * 2.1f * haloScale,
            center = center,
        )

        // Sphère : cœur lumineux → bord sombre.
        val sphereAlpha = if (state == OrbState.Offline) 0.35f else 1f
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(
                    core.copy(alpha = 0.85f * sphereAlpha),
                    core.copy(alpha = 0.30f * sphereAlpha),
                    JarvisColors.Bg.copy(alpha = 0.9f),
                ),
                center = center + Offset(-baseRadius * 0.18f, -baseRadius * 0.22f),
                radius = baseRadius * 1.35f,
            ),
            radius = baseRadius,
            center = center,
        )

        // Reflet supérieur (verre).
        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(Color.White.copy(alpha = 0.28f * sphereAlpha), Color.Transparent),
                center = center + Offset(-baseRadius * 0.3f, -baseRadius * 0.45f),
                radius = baseRadius * 0.6f,
            ),
            radius = baseRadius * 0.6f,
            center = center + Offset(-baseRadius * 0.3f, -baseRadius * 0.45f),
        )

        when (state) {
            OrbState.Recording -> drawRecordingRing(center, baseRadius, smoothAmplitude, animate)
            OrbState.Processing -> drawProcessingArc(center, baseRadius, rotation, animate)
            OrbState.Speaking -> drawSpeakingWaves(center, baseRadius, wave, core, animate)
            OrbState.Error -> drawStaticRing(center, baseRadius, JarvisColors.Red.copy(alpha = 0.5f))
            OrbState.Offline -> drawStaticRing(center, baseRadius, JarvisColors.TextTertiary.copy(alpha = 0.4f))
            OrbState.Idle -> Unit
        }
    }
}

private fun DrawScope.drawStaticRing(center: Offset, radius: Float, color: Color) {
    drawCircle(
        color = color,
        radius = radius * 1.28f,
        center = center,
        style = Stroke(width = 2.dp.toPx()),
    )
}

private fun DrawScope.drawRecordingRing(
    center: Offset,
    radius: Float,
    amplitude: Float,
    animate: Boolean,
) {
    val ringRadius = radius * (1.25f + (if (animate) amplitude * 0.25f else 0f))
    drawCircle(
        color = JarvisColors.Red.copy(alpha = 0.65f),
        radius = ringRadius,
        center = center,
        style = Stroke(width = 3.dp.toPx()),
    )
}

private fun DrawScope.drawProcessingArc(
    center: Offset,
    radius: Float,
    rotation: Float,
    animate: Boolean,
) {
    val arcRadius = radius * 1.3f
    drawArc(
        brush = Brush.sweepGradient(
            colors = listOf(
                Color.Transparent,
                JarvisColors.Cyan.copy(alpha = 0.9f),
            ),
            center = center,
        ),
        startAngle = if (animate) rotation else 300f,
        sweepAngle = 270f,
        useCenter = false,
        topLeft = Offset(center.x - arcRadius, center.y - arcRadius),
        size = androidx.compose.ui.geometry.Size(arcRadius * 2f, arcRadius * 2f),
        style = Stroke(width = 3.dp.toPx()),
    )
}

private fun DrawScope.drawSpeakingWaves(
    center: Offset,
    radius: Float,
    progress: Float,
    color: Color,
    animate: Boolean,
) {
    if (!animate) {
        // Statique : deux anneaux fixes signalent la lecture.
        drawCircle(color.copy(alpha = 0.4f), radius * 1.25f, center, style = Stroke(2.dp.toPx()))
        drawCircle(color.copy(alpha = 0.2f), radius * 1.55f, center, style = Stroke(2.dp.toPx()))
        return
    }
    // Deux ondes déphasées qui s'étendent et s'estompent.
    listOf(progress, (progress + 0.5f) % 1f).forEach { p ->
        val waveRadius = radius * (1.15f + p * 0.75f)
        drawCircle(
            color = color.copy(alpha = (1f - p) * 0.45f),
            radius = waveRadius,
            center = center,
            style = Stroke(width = 2.dp.toPx()),
        )
    }
}
