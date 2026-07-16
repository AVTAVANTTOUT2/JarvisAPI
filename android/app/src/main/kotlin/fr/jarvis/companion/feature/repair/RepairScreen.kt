package fr.jarvis.companion.feature.repair

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings

@Composable
fun RepairScreen(
    onNeedsOnboarding: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Réparation", "Réinitialisation locale")

        JarvisCard(title = "Jeton d'appairage") {
            Text(
                "Révoque le jeton stocké sur ce téléphone. Vous devrez saisir un nouveau code depuis le Mac.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Button(
                onClick = {
                    JarvisSettings.clearNativeToken(context)
                    context.appContainer().repository.invalidateHttpCache()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Révoquer le jeton local")
            }
        }

        JarvisCard(title = "Onboarding") {
            Text(
                "Relance l'assistant de configuration (serveur, appairage).",
                style = MaterialTheme.typography.bodyMedium,
            )
            TextButton(
                onClick = {
                    JarvisSettings.setOnboardingComplete(context, false)
                    JarvisSettings.clearNativeToken(context)
                    onNeedsOnboarding()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Relancer l'onboarding", fontWeight = FontWeight.Medium)
            }
        }
    }
}
