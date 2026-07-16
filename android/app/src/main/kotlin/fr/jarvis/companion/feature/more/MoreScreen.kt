package fr.jarvis.companion.feature.more

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.navigation.JarvisDestination

data class MoreMenuItem(
    val title: String,
    val subtitle: String,
    val route: String,
)

private val moreItems = listOf(
    MoreMenuItem("Tâches", "Liste complète", JarvisDestination.TASKS),
    MoreMenuItem("Localisation", "Présence GPS", JarvisDestination.LOCATION),
    MoreMenuItem("Notifications", "Alertes JARVIS", JarvisDestination.NOTIFICATIONS),
    MoreMenuItem("Diagnostics", "État technique", JarvisDestination.DIAGNOSTICS),
    MoreMenuItem("Réglages", "Connexion et services", JarvisDestination.SETTINGS),
    MoreMenuItem("Réparation", "Réappairage et reset", JarvisDestination.REPAIR),
)

@Composable
fun MoreScreen(
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text("Plus", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        moreItems.forEach { item ->
            ListItem(
                headlineContent = { Text(item.title) },
                supportingContent = { Text(item.subtitle) },
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onNavigate(item.route) },
            )
        }
    }
}
