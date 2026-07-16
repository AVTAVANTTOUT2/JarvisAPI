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
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.JarvisFutureAction
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.core.ui.components.JarvisStatusBadge
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.StatusTone
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings

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
    var serverFeedback by remember { mutableStateOf<String?>(null) }
    var serverErrorMessage by remember { mutableStateOf<String?>(null) }
    var porcupineKey by remember { mutableStateOf("") }
    var keyErrorMessage by remember { mutableStateOf<String?>(null) }
    var voiceFeedback by remember { mutableStateOf<String?>(null) }
    val futureOptions = remember { buildFutureSettingsOptions() }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Réglages", "Connexion, sécurité et services locaux")

        JarvisCard(title = "Connexion") {
            OutlinedTextField(
                value = serverUrl,
                onValueChange = {
                    serverUrl = it
                    serverErrorMessage = null
                    serverFeedback = null
                },
                label = { Text("Serveur HTTPS") },
                isError = serverErrorMessage != null,
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            serverErrorMessage?.let { ErrorCallout(it) }
            serverFeedback?.let { JarvisStatusBadge(it, tone = StatusTone.Info) }
            JarvisPrimaryButton(
                text = "Enregistrer le serveur",
                onClick = {
                    val saveResult = evaluateServerSave(
                        rawInput = serverUrl,
                        currentServer = JarvisSettings.server(context),
                    )
                    if (saveResult.errorMessage != null) {
                        serverErrorMessage = saveResult.errorMessage
                        serverFeedback = null
                    } else {
                        val normalized = saveResult.normalizedServerUrl ?: return@JarvisPrimaryButton
                        JarvisSettings.setServer(context, normalized)
                        if (saveResult.shouldRevokeLocalToken) {
                            JarvisSettings.clearNativeToken(context)
                            context.appContainer().repository.invalidateHttpCache()
                        }
                        serverErrorMessage = null
                        serverFeedback = saveResult.successMessage
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                "Appareil : ${JarvisSettings.deviceId(context)}",
                style = MaterialTheme.typography.bodySmall,
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
                onValueChange = {
                    porcupineKey = it
                    keyErrorMessage = null
                    voiceFeedback = null
                },
                label = { Text("Clé Picovoice") },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            keyErrorMessage?.let { ErrorCallout(it) }
            voiceFeedback?.let { JarvisStatusBadge(it, tone = StatusTone.Info) }
            JarvisPrimaryButton(
                text = "Enregistrer la clé",
                onClick = {
                    val sanitized = sanitizePorcupineKey(porcupineKey)
                    if (sanitized != null) {
                        onPorcupineKeySave(sanitized)
                        porcupineKey = ""
                        voiceFeedback = "Clé enregistrée."
                        keyErrorMessage = null
                    } else {
                        keyErrorMessage = "Clé Picovoice vide"
                        voiceFeedback = null
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }

        JarvisCard(title = "Localisation") {
            SettingsToggle(
                title = "Présence GPS",
                subtitle = "Service de premier plan vers le Mac",
                checked = locationEnabled,
                onCheckedChange = onLocationToggle,
            )
            Text(
                "La logique permission/service reste inchangée et pilotée par MainActivity.",
                style = MaterialTheme.typography.bodySmall,
            )
        }

        JarvisCard(title = "Notifications") {
            Text(
                if (BuildConfig.FIREBASE_CONFIGURED) {
                    "Firebase configuré — jetons enregistrés après appairage."
                } else {
                    "Non configuré dans ce build (google-services.json absent)."
                },
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        JarvisCard(title = "Données") {
            Text(
                "Serveur actuel : ${JarvisSettings.server(context)}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "Token local : ${if (JarvisSettings.nativeToken(context).isBlank()) "absent" else "présent"}",
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                "Aucune synchronisation de données mockées : cache et stockage sécurisé natifs uniquement.",
                style = MaterialTheme.typography.bodySmall,
            )
        }

        JarvisCard(title = "Sécurité & apparence") {
            Text(
                "Thème sombre JARVIS et verrouillage côté serveur conservés.",
                style = MaterialTheme.typography.bodyMedium,
            )
            futureOptions.forEach { option ->
                JarvisFutureAction(
                    title = option.title,
                    description = option.description,
                )
            }
        }

        JarvisCard(title = "À propos") {
            Text(
                "JARVIS Companion ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "Companion Android natif, sans WebView.",
                style = MaterialTheme.typography.bodySmall,
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
