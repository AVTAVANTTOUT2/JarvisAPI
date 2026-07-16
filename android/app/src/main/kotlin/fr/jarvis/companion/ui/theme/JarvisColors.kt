package fr.jarvis.companion.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Palette JARVIS — alignée sur le frontend web
 * (`frontend/src/app/globals.css`, `web/src/pages/mission-control.css`).
 *
 * Règle : le cyan est réservé à JARVIS (orbe, présence, primaire). Les états
 * utilisent green/amber/red ; tout le reste est verre et gris bleuté.
 */
object JarvisColors {
    // Fond
    val Bg = Color(0xFF0A0A0F)
    val BgTop = Color(0xFF0D1017)
    val Surface = Color(0xFF12151E)
    val SurfaceRaised = Color(0xFF181C28)

    // Accents
    val Cyan = Color(0xFF00D4FF)
    val Blue = Color(0xFF4A9EFF)
    val Purple = Color(0xFF9C59FF)
    val Green = Color(0xFF30D158)
    val Amber = Color(0xFFFFD60A)
    val Red = Color(0xFFFF453A)

    // Texte
    val TextPrimary = Color(0xFFF2F5FA)
    val TextSecondary = Color(0xFF9AA6B5)
    val TextTertiary = Color(0xFF5E6B7C)

    // Verre et hairlines (alphas sur blanc, comme le web)
    val GlassTop = Color.White.copy(alpha = 0.06f)
    val GlassBottom = Color.White.copy(alpha = 0.025f)
    val BorderTop = Color.White.copy(alpha = 0.14f)
    val BorderBottom = Color.White.copy(alpha = 0.05f)
    val Hairline = Color.White.copy(alpha = 0.08f)

    // Bulles utilisateur (bleu nuit, distinct du verre JARVIS)
    val UserBubbleTop = Color(0xFF1B3A5C)
    val UserBubbleBottom = Color(0xFF142B45)
}
