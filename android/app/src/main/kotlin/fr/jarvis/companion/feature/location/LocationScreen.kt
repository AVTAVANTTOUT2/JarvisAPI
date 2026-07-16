package fr.jarvis.companion.feature.location

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.NetworkStatusBadge
import fr.jarvis.companion.core.ui.components.SectionHeader

@Composable
fun LocationScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val container = context.appContainer()
    val viewModel: LocationViewModel = viewModel(
        factory = LocationViewModelFactory(container, context),
    )
    val state by viewModel.uiState.collectAsState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Localisation", "File offline-first, sans coordonnées affichées")

        JarvisCard(title = "Collecte") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Activer la collecte GPS")
                Switch(
                    checked = state.collectionEnabled,
                    onCheckedChange = { enabled ->
                        viewModel.toggleCollection(enabled) {
                            val intent = Intent(
                                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.parse("package:${context.packageName}"),
                            )
                            context.startActivity(intent)
                        }
                    },
                )
            }
            Text(
                "Permissions : fine ${if (state.finePermission) "accordée" else "refusée"}, " +
                    "arrière-plan ${if (state.backgroundPermission) "accordé" else "refusé"}",
                style = MaterialTheme.typography.bodySmall,
            )
            Text("Mode fréquence : ${state.frequencyMode}", style = MaterialTheme.typography.bodyMedium)
            NetworkStatusBadge(state = state.connectivity)
        }

        JarvisCard(title = "État") {
            Text("En attente : ${state.pendingCount}")
            Text("Échecs permanents : ${state.failedCount}")
            Text("Invalides : ${state.invalidCount}")
            Text("Dernière capture : ${state.lastCaptureTime ?: "—"} ${state.lastCaptureAccuracy ?: ""}")
            Text("Dernière sync : ${state.lastSyncRelative}")
            if (state.lastSyncAbsolute != null) {
                Text("(${state.lastSyncAbsolute})", style = MaterialTheme.typography.bodySmall)
            }
        }

        JarvisCard(title = "Timeline") {
            if (state.timeline.isEmpty()) {
                Text("Aucun événement récent.")
            } else {
                state.timeline.forEach { entry ->
                    Text("${entry.timeLabel}   ${entry.label}")
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Button(
                onClick = { viewModel.syncNow() },
                enabled = !state.isSyncing,
                modifier = Modifier.weight(1f),
            ) {
                Text(if (state.isSyncing) "Sync…" else "Synchroniser")
            }
            OutlinedButton(
                onClick = {
                    val intent = Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:${context.packageName}"),
                    )
                    context.startActivity(intent)
                },
                modifier = Modifier.weight(1f),
            ) {
                Text("Permissions")
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = { viewModel.requestClearPendingConfirm() },
                modifier = Modifier.weight(1f),
            ) {
                Text("Vider en attente")
            }
            OutlinedButton(
                onClick = { viewModel.clearInvalid() },
                modifier = Modifier.weight(1f),
            ) {
                Text("Supprimer invalides")
            }
        }

        state.message?.let { msg ->
            Text(msg, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        }
    }

    if (state.showClearPendingConfirm) {
        AlertDialog(
            onDismissRequest = { viewModel.dismissClearPendingConfirm() },
            title = { Text("Vider la file en attente ?") },
            text = { Text("Les points non synchronisés seront supprimés définitivement.") },
            confirmButton = {
                TextButton(onClick = { viewModel.clearPending() }) {
                    Text("Confirmer")
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.dismissClearPendingConfirm() }) {
                    Text("Annuler")
                }
            },
        )
    }
}
