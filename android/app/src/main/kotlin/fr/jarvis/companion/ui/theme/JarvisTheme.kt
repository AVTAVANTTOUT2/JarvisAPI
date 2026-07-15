package fr.jarvis.companion.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val JarvisColors = darkColorScheme(
    primary = Color(0xFF6EA8FE),
    onPrimary = Color(0xFF0A0A0F),
    background = Color(0xFF0A0A0F),
    onBackground = Color(0xFFE6EDF3),
    surface = Color(0xFF14141C),
    onSurface = Color(0xFFE6EDF3),
    error = Color(0xFFFF6B6B),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = JarvisColors,
        content = content,
    )
}
