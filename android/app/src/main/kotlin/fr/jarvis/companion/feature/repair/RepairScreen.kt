package fr.jarvis.companion.feature.repair

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.JarvisStatusBadge
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.components.StatusTone
import fr.jarvis.companion.data.JarvisSettings

@Composable
fun RepairScreen(
    onNeedsOnboarding: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var pendingAction by remember { mutableStateOf<RepairActionModel?>(null) }
    var feedback by remember { mutableStateOf<String?>(null) }
    val actions = remember { buildRepairActions() }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Réparation", "Zone danger — confirmations obligatoires")

        JarvisGlassCard(
            title = "Zone danger",
            variant = GlassVariant.Danger,
        ) {
            Text(
                "Aucune suppression de données supplémentaires. Ces actions ciblent uniquement le jeton d'appairage local et la relance de l'onboarding.",
                style = MaterialTheme.typography.bodyMedium,
            )
            actions.forEach { action ->
                JarvisCard(title = action.title) {
                    Text(action.description, style = MaterialTheme.typography.bodyMedium)
                    JarvisSecondaryButton(
                        text = action.ctaLabel,
                        onClick = { pendingAction = action },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        feedback?.let { message ->
            JarvisStatusBadge(
                label = message,
                tone = StatusTone.Info,
            )
        }
    }

    pendingAction?.let { action ->
        AlertDialog(
            onDismissRequest = { pendingAction = null },
            title = { Text(action.confirmTitle) },
            text = { Text(action.confirmMessage) },
            confirmButton = {
                TextButton(
                    onClick = {
                        when (action.type) {
                            RepairActionType.RevokeToken -> {
                                JarvisSettings.clearNativeToken(context)
                                context.appContainer().repository.invalidateHttpCache()
                                feedback = "Jeton local révoqué."
                            }
                            RepairActionType.RelaunchOnboarding -> {
                                JarvisSettings.setOnboardingComplete(context, false)
                                JarvisSettings.clearNativeToken(context)
                                context.appContainer().repository.invalidateHttpCache()
                                onNeedsOnboarding()
                            }
                        }
                        pendingAction = null
                    },
                ) { Text("Confirmer") }
            },
            dismissButton = {
                TextButton(onClick = { pendingAction = null }) {
                    Text("Annuler")
                }
            },
        )
    }
}
