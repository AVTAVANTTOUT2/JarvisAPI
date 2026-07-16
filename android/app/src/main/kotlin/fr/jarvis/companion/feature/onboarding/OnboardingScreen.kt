package fr.jarvis.companion.feature.onboarding

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.ServerUrlNormalizer
import kotlinx.coroutines.launch

private const val STEP_COUNT = 5

@Composable
fun OnboardingScreen(
    onComplete: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val container = context.appContainer()
    val scope = rememberCoroutineScope()

    var step by remember { mutableIntStateOf(0) }
    var serverUrl by remember { mutableStateOf(JarvisSettings.server(context).ifBlank { BuildConfig.DEFAULT_SERVER }) }
    var serverError by remember { mutableStateOf<String?>(null) }
    var pairingCode by remember { mutableStateOf("") }
    var pairingError by remember { mutableStateOf<String?>(null) }
    var isLoading by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        LinearProgressIndicator(
            progress = { (step + 1f) / STEP_COUNT },
            modifier = Modifier.fillMaxWidth(),
        )
        Text("Étape ${step + 1} / $STEP_COUNT", style = MaterialTheme.typography.labelMedium)

        when (step) {
            0 -> OnboardingWelcome()
            1 -> OnboardingServer(
                url = serverUrl,
                onUrlChange = { serverUrl = it; serverError = null },
                error = serverError,
                hint = BuildConfig.DEFAULT_SERVER,
            )
            2 -> OnboardingPairing(
                code = pairingCode,
                onCodeChange = { pairingCode = it.filter(Char::isDigit).take(6); pairingError = null },
                error = pairingError,
            )
            3 -> OnboardingPermissions()
            4 -> OnboardingDone()
        }

        Spacer(Modifier.weight(1f))

        if (isLoading) {
            CircularProgressIndicator()
        }

        RowActions(
            step = step,
            onBack = { if (step > 0) step -= 1 },
            onNext = {
                when (step) {
                    0 -> step = 1
                    1 -> {
                        val normalized = ServerUrlNormalizer.normalize(serverUrl)
                        if (normalized == null) {
                            serverError = "Adresse HTTPS invalide"
                        } else {
                            JarvisSettings.setServer(context, normalized)
                            container.repository.invalidateHttpCache()
                            step = 2
                        }
                    }
                    2 -> {
                        if (pairingCode.length != 6) {
                            pairingError = "Code à six chiffres requis"
                        } else {
                            isLoading = true
                            scope.launch {
                                val result = container.repository.completePairing(pairingCode)
                                isLoading = false
                                if (result.ok) {
                                    val token = result.json.optString("token", "")
                                    if (token.isNotEmpty()) {
                                        JarvisSettings.setNativeToken(context, token)
                                        pairingError = null
                                        step = 3
                                    } else {
                                        pairingError = "Réponse serveur invalide"
                                    }
                                } else {
                                    pairingError = result.error
                                }
                            }
                        }
                    }
                    3 -> step = 4
                    4 -> {
                        JarvisSettings.setOnboardingComplete(context, true)
                        onComplete()
                    }
                }
            },
        )
    }
}

@Composable
private fun OnboardingWelcome() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Bienvenue", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "JARVIS Companion est le compagnon natif de votre Mac : briefing, tâches, agenda et voix, sans WebView.",
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

@Composable
private fun OnboardingServer(
    url: String,
    onUrlChange: (String) -> Unit,
    error: String?,
    hint: String,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Serveur JARVIS", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("Adresse HTTPS du Mac (Tailscale ou LAN). Émulateur : $hint")
        OutlinedTextField(
            value = url,
            onValueChange = onUrlChange,
            isError = error != null,
            supportingText = error?.let { { Text(it) } },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun OnboardingPairing(
    code: String,
    onCodeChange: (String) -> Unit,
    error: String?,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Appairage", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("Sur le Mac : JARVIS → Téléphone → générer un code à six chiffres.")
        OutlinedTextField(
            value = code,
            onValueChange = onCodeChange,
            isError = error != null,
            supportingText = error?.let { { Text(it) } },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun OnboardingPermissions() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Permissions", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("GPS, micro et notifications seront demandés au moment où vous activerez chaque fonction dans Réglages — pas tout d'un coup.")
        Spacer(Modifier.height(4.dp))
        Text("• Localisation : présence GPS vers le Mac", style = MaterialTheme.typography.bodyMedium)
        Text("• Micro : wake word et conversation vocale", style = MaterialTheme.typography.bodyMedium)
        Text("• Notifications : alertes urgentes JARVIS", style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun OnboardingDone() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Prêt", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("Votre téléphone est configuré. L'accueil se synchronisera dès que le Mac sera joignable.")
    }
}

@Composable
private fun RowActions(
    step: Int,
    onBack: () -> Unit,
    onNext: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = onNext, modifier = Modifier.fillMaxWidth()) {
            Text(if (step == STEP_COUNT - 1) "Commencer" else "Continuer")
        }
        if (step > 0 && step < STEP_COUNT - 1) {
            TextButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
                Text("Retour")
            }
        }
    }
}
