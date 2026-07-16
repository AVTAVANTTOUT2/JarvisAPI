package fr.jarvis.companion.feature.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.ServerUrlNormalizer

@Composable
fun SettingsScreen(
    locationEnabled: Boolean,
    wakeEnabled: Boolean,
    hasPorcupineKey: Boolean,
    onLocationToggle: (Boolean) -> Unit,
    onWakeToggle: (Boolean) -> Unit,
    onPorcupineKeySave: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var serverUrl by remember { mutableStateOf(JarvisSettings.server(context)) }
    var serverError by remember { mutableStateOf<String?>(null) }
    var porcupineKey by remember { mutableStateOf("") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Réglages", "Connexion et services")

        JarvisCard(title = "Connexion") {
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it; serverError = null },
                label = { Text("Serveur HTTPS") },
                isError = serverError != null,
                supportingText = serverError?.let { { Text(it) } },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            TextButton(
                onClick = {
                    val normalized = ServerUrlNormalizer.normalize(serverUrl)
                    if (normalized == null) {
                        serverError = "Adresse invalide"
                    } else {
                        val changed = normalized != JarvisSettings.server(context)
                        JarvisSettings.setServer(context, normalized)
                        if (changed) {
                            JarvisSettings.clearNativeToken(context)
                            context.appContainer().repository.invalidateHttpCache()
                        }
                        serverError = null
                    }
                },
            ) { Text("Enregistrer le serveur") }
            Text(
                "Appareil : ${JarvisSettings.deviceId(context)}",
                style = MaterialTheme.typography.bodySmall,
            )
        }

        JarvisCard(title = "Localisation") {
            SettingsToggle(
                title = "Présence GPS",
                subtitle = "Service de premier plan vers le Mac",
                checked = locationEnabled,
                onCheckedChange = onLocationToggle,
            )
        }

        JarvisCard(title = "Voix") {
            SettingsToggle(
                title = "Mot « JARVIS » (Porcupine)",
                subtitle = if (hasPorcupineKey) "Clé configurée" else "Clé Picovoice requise",
                checked = wakeEnabled,
                onCheckedChange = onWakeToggle,
            )
            OutlinedTextField(
                value = porcupineKey,
                onValueChange = { porcupineKey = it },
                label = { Text("Clé Picovoice") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            TextButton(
                onClick = {
                    if (porcupineKey.isNotBlank()) {
                        onPorcupineKeySave(porcupineKey.trim())
                        porcupineKey = ""
                    }
                },
            ) { Text("Enregistrer la clé") }
        }

        JarvisCard(title = "Notifications push") {
            Text(
                if (BuildConfig.FIREBASE_CONFIGURED) {
                    "Firebase configuré — jetons enregistrés après appairage."
                } else {
                    "Non configuré dans ce build (google-services.json absent)."
                },
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun SettingsToggle(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, fontWeight = FontWeight.Medium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
