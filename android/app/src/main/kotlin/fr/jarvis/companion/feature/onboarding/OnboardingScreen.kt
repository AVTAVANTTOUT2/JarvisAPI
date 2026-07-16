package fr.jarvis.companion.feature.onboarding

import android.content.res.Configuration
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Surface
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisOrb
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.OrbState
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.ServerUrlNormalizer
import fr.jarvis.companion.ui.theme.JarvisColors
import fr.jarvis.companion.ui.theme.JarvisSpacing
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
    val configuration = androidx.compose.ui.platform.LocalConfiguration.current
    val fontScale = androidx.compose.ui.platform.LocalDensity.current.fontScale
    val useLandscapeLayout = configuration.orientation == Configuration.ORIENTATION_LANDSCAPE && fontScale < 1.35f
    val onBack = remember(step) { { if (step > 0) step -= 1 } }
    val onNext = {
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
                val validation = validatePairingCode(pairingCode)
                if (validation != null) {
                    pairingError = validation
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
    }

    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
    ) {
        val horizontalPadding = if (maxWidth < 420.dp) JarvisSpacing.lg else 28.dp
        val verticalPadding = if (maxHeight < 620.dp) JarvisSpacing.lg else 28.dp
        val layoutModifier = Modifier
            .fillMaxSize()
            .padding(horizontal = horizontalPadding, vertical = verticalPadding)

        if (useLandscapeLayout) {
            Row(
                modifier = layoutModifier,
                horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.lg),
            ) {
                OnboardingIdentityCard(
                    step = step,
                    modifier = Modifier.weight(0.95f),
                    isLoading = isLoading,
                )
                OnboardingStepCard(
                    step = step,
                    serverUrl = serverUrl,
                    serverError = serverError,
                    pairingCode = pairingCode,
                    pairingError = pairingError,
                    isLoading = isLoading,
                    onServerChange = {
                        serverUrl = it
                        serverError = null
                    },
                    onPairingCodeChange = {
                        pairingCode = sanitizePairingCode(it)
                        pairingError = null
                    },
                    onBack = onBack,
                    onNext = onNext,
                    modifier = Modifier.weight(1.4f),
                )
            }
        } else {
            Column(
                modifier = layoutModifier,
                verticalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
            ) {
                OnboardingIdentityCard(
                    step = step,
                    modifier = Modifier.fillMaxWidth(),
                    isLoading = isLoading,
                )
                OnboardingStepCard(
                    step = step,
                    serverUrl = serverUrl,
                    serverError = serverError,
                    pairingCode = pairingCode,
                    pairingError = pairingError,
                    isLoading = isLoading,
                    onServerChange = {
                        serverUrl = it
                        serverError = null
                    },
                    onPairingCodeChange = {
                        pairingCode = sanitizePairingCode(it)
                        pairingError = null
                    },
                    onBack = onBack,
                    onNext = onNext,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun OnboardingIdentityCard(
    step: Int,
    isLoading: Boolean,
    modifier: Modifier = Modifier,
) {
    JarvisGlassCard(
        modifier = modifier,
        variant = if (isLoading) GlassVariant.Accent else GlassVariant.Default,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.md),
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        ) {
            JarvisOrb(
                state = if (isLoading) OrbState.Processing else OrbState.Idle,
                size = 92.dp,
                stateDescription = if (isLoading) "JARVIS traite l'appairage" else "JARVIS prêt pour la configuration",
            )
            Column(verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.weight(1f)) {
                Text(
                    text = "JARVIS",
                    style = MaterialTheme.typography.headlineSmall,
                    color = JarvisColors.TextPrimary,
                )
                Text(
                    text = "Companion Android",
                    style = MaterialTheme.typography.bodyMedium,
                    color = JarvisColors.TextSecondary,
                )
                Text(
                    text = "Étape ${step + 1} / $STEP_COUNT",
                    style = MaterialTheme.typography.labelMedium,
                    color = JarvisColors.TextSecondary,
                )
            }
        }
        OnboardingStepper(
            currentStep = step,
            stepCount = STEP_COUNT,
        )
    }
}

@Composable
fun OnboardingStepper(
    currentStep: Int,
    stepCount: Int,
    modifier: Modifier = Modifier,
) {
    val progressStates = buildStepperStates(currentStep = currentStep, stepCount = stepCount)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .semantics { contentDescription = "Progression onboarding étape ${currentStep + 1} sur $stepCount" },
        horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
    ) {
        progressStates.forEach { state ->
            val color = when {
                state.current -> JarvisColors.Cyan
                state.completed -> JarvisColors.Green
                else -> JarvisColors.TextTertiary
            }
            Surface(
                modifier = Modifier
                    .weight(1f)
                    .height(8.dp),
                color = color.copy(alpha = if (state.current) 1f else 0.5f),
                shape = CircleShape,
            ) {}
        }
    }
}

@Composable
private fun OnboardingStepCard(
    step: Int,
    serverUrl: String,
    serverError: String?,
    pairingCode: String,
    pairingError: String?,
    isLoading: Boolean,
    onServerChange: (String) -> Unit,
    onPairingCodeChange: (String) -> Unit,
    onBack: () -> Unit,
    onNext: () -> Unit,
    modifier: Modifier = Modifier,
) {
    JarvisGlassCard(
        modifier = modifier.heightIn(min = 300.dp),
        variant = GlassVariant.Accent,
    ) {
        when (step) {
            0 -> OnboardingWelcome()
            1 -> OnboardingServer(
                url = serverUrl,
                onUrlChange = onServerChange,
                error = serverError,
                hint = BuildConfig.DEFAULT_SERVER,
            )
            2 -> OnboardingPairing(
                code = pairingCode,
                onCodeChange = onPairingCodeChange,
                error = pairingError,
            )
            3 -> OnboardingPermissions()
            4 -> OnboardingDone()
        }

        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(JarvisSpacing.sm),
        ) {
            if (step > 0 && step < STEP_COUNT - 1) {
                JarvisSecondaryButton(
                    text = "Retour",
                    onClick = onBack,
                    modifier = Modifier.weight(1f),
                    enabled = !isLoading,
                )
            }
            JarvisPrimaryButton(
                text = if (step == STEP_COUNT - 1) "Commencer" else "Continuer",
                onClick = onNext,
                loading = isLoading,
                enabled = !isLoading,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun OnboardingWelcome() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader(
            title = "Bienvenue",
            subtitle = "Configuration initiale du compagnon natif",
        )
        Text(
            "JARVIS Companion relie ce téléphone à ton Mac pour la voix, la localisation et les notifications urgentes.",
            style = MaterialTheme.typography.bodyLarge,
            color = JarvisColors.TextPrimary,
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
        SectionHeader("Connexion serveur", "Adresse HTTPS du Mac")
        Text(
            "Exemple émulateur : $hint",
            style = MaterialTheme.typography.bodySmall,
            color = JarvisColors.TextSecondary,
        )
        OutlinedTextField(
            value = url,
            onValueChange = onUrlChange,
            isError = error != null,
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let { ErrorCallout(it) }
    }
}

@Composable
private fun OnboardingPairing(
    code: String,
    onCodeChange: (String) -> Unit,
    error: String?,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader("Appairage", "Code à six chiffres")
        Text(
            "Depuis le Mac : JARVIS -> Téléphone -> Générer un code.",
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisColors.TextSecondary,
        )
        OutlinedTextField(
            value = code,
            onValueChange = onCodeChange,
            isError = error != null,
            singleLine = true,
            textStyle = MaterialTheme.typography.headlineSmall.copy(
                letterSpacing = 6.sp,
                fontFamily = FontFamily.Monospace,
                textAlign = TextAlign.Center,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = "Champ code six chiffres" },
        )
        error?.let { ErrorCallout(it) }
    }
}

@Composable
private fun OnboardingPermissions() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader("Permissions", "Demandées uniquement quand nécessaire")
        Text(
            "• Localisation : présence GPS vers le Mac\n" +
                "• Micro : wake word et conversation vocale\n" +
                "• Notifications : alertes urgentes JARVIS",
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisColors.TextPrimary,
        )
    }
}

@Composable
private fun OnboardingDone() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader("Prêt", "Configuration terminée")
        Text(
            "Ton téléphone est appairé. L'accueil se synchronisera dès que le Mac sera joignable.",
            style = MaterialTheme.typography.bodyLarge,
            color = JarvisColors.TextPrimary,
        )
    }
}
