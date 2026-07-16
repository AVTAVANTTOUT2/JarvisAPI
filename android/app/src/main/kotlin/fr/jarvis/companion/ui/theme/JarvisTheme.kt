package fr.jarvis.companion.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val JarvisColors = darkColorScheme(
    primary = Color(0xFF00D4FF),
    onPrimary = Color(0xFF0A0F18),
    secondary = Color(0xFF6EA8FE),
    onSecondary = Color(0xFF0A0F18),
    tertiary = Color(0xFF94A3B8),
    background = Color(0xFF0A0F18),
    onBackground = Color(0xFFE6EDF3),
    surface = Color(0xFF0F1724),
    onSurface = Color(0xFFE6EDF3),
    surfaceVariant = Color(0xFF1A2332),
    onSurfaceVariant = Color(0xFF94A3B8),
    error = Color(0xFFFF6B6B),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = JarvisColors,
        content = content,
    )
}
