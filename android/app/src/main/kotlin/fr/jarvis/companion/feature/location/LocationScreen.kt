package fr.jarvis.companion.feature.location

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.location.CaptureCadenceMode
import fr.jarvis.companion.core.ui.components.ErrorCallout
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisComingSoonCard
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisListItem
import fr.jarvis.companion.core.ui.components.JarvisMetric
import fr.jarvis.companion.core.ui.components.JarvisOfflineBanner
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.JarvisStatusBadge
import fr.jarvis.companion.core.ui.components.NetworkStatusBadge
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.components.StatusTone

@OptIn(ExperimentalLayoutApi::class)
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
        val heroVerdict = deriveLocationHeroVerdict(state)
        val captureSummary = formatLastCaptureSummary(state.lastCaptureTime, state.lastCaptureAccuracy)

        SectionHeader("Localisation", "Collecte réelle, synchronisation réelle, coordonnées masquées")

        JarvisGlassCard(
            variant = when (heroVerdict.level) {
                LocationHealthLevel.Healthy -> GlassVariant.Accent
                LocationHealthLevel.Attention -> GlassVariant.Default
                LocationHealthLevel.Problem -> GlassVariant.Danger
            },
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    heroVerdict.title,
                    style = MaterialTheme.typography.titleMedium,
                )
                JarvisStatusBadge(
                    label = heroVerdict.badgeLabel,
                    tone = locationHealthTone(heroVerdict.level),
                )
            }
            Text(
                heroVerdict.detail,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                state.userStatus,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
        }

        if (state.connectivity == ConnectivityState.Offline) {
            JarvisOfflineBanner(text = "Hors ligne — les points restent stockés localement")
        }
        if (state.connectivity == ConnectivityState.Unauthorized) {
            ErrorCallout("Session expirée : jeton révoqué. Réappairage requis.")
        }

        JarvisGlassCard(title = "Collecte, permissions et connectivité") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Activer la collecte GPS", style = MaterialTheme.typography.bodyLarge)
                Switch(
                    checked = state.collectionEnabled,
                    modifier = Modifier.semantics { contentDescription = "Activer la collecte GPS" },
                    onCheckedChange = { enabled ->
                        viewModel.toggleCollection(enabled) {
                            openAppSettings(context)
                        }
                    },
                )
            }
            JarvisListItem(
                title = "Permissions",
                subtitle = "Fine ${if (state.finePermission) "accordée" else "refusée"} • " +
                    "Arrière-plan ${if (state.backgroundPermission) "accordée" else "refusée"}",
                trailing = {
                    JarvisSecondaryButton(
                        text = "Ouvrir",
                        onClick = { openAppSettings(context) },
                        modifier = Modifier.width(110.dp),
                    )
                },
            )
            NetworkStatusBadge(state = state.connectivity)
        }

        JarvisGlassCard(title = "Cadence de capture") {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CaptureCadenceMode.entries.forEach { mode ->
                    FilterChip(
                        selected = state.cadenceMode == mode,
                        onClick = { viewModel.setCadence(mode) },
                        label = { Text(mode.labelFr()) },
                    )
                }
            }
        }

        JarvisGlassCard(title = "Métriques") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(modifier = Modifier.weight(1f)) {
                    JarvisMetric(
                        value = state.pendingCount.toString(),
                        label = "En attente",
                        tone = if (state.pendingCount > 0) StatusTone.Warning else StatusTone.Positive,
                    )
                }
                Box(modifier = Modifier.weight(1f)) {
                    JarvisMetric(
                        value = state.sendingCount.toString(),
                        label = "Envoi",
                        tone = if (state.sendingCount > 0) StatusTone.Info else StatusTone.Neutral,
                    )
                }
                Box(modifier = Modifier.weight(1f)) {
                    JarvisMetric(
                        value = state.lastSyncRelative,
                        label = "Dernière sync",
                        tone = when (state.connectivity) {
                            ConnectivityState.ServerReachable -> StatusTone.Positive
                            ConnectivityState.Unauthorized -> StatusTone.Danger
                            else -> StatusTone.Warning
                        },
                    )
                }
            }
            Text("Dernière capture : $captureSummary", style = MaterialTheme.typography.bodyMedium)
            Text(
                "Échecs permanents : ${state.failedCount} • Invalides : ${state.invalidCount}",
                style = MaterialTheme.typography.bodySmall,
            )
            if (state.lastSyncAbsolute != null) {
                Text("(${state.lastSyncAbsolute})", style = MaterialTheme.typography.bodySmall)
            }
        }

        JarvisGlassCard(title = "Chaîne runtime") {
            val rc = state.runtimeCounters
            Text("Service : ${if (rc.serviceRunning) "actif" else "arrêté"}")
            Text("Moteur : ${if (rc.engineStarted) "démarré" else "arrêté"}")
            Text(
                "GPS système : ${if (rc.gpsEnabled) "on" else "off"} — " +
                    "Réseau : ${if (rc.networkEnabled) "on" else "off"}",
            )
            Text("Callbacks : ${formatTs(rc.callbacks)}")
            Text("Acceptés : ${formatTs(rc.accepted)} — Rejetés : ${formatTs(rc.rejected)}")
            Text("Insérés localement : ${formatTs(rc.inserted)}")
            Text("Dernier HTTP sync : ${rc.lastHttpStatus} (${rc.lastBatchAccepted} acceptés)")
        }

        JarvisGlassCard(title = "Diagnostics serveur") {
            val diag = state.serverDiagnostics
            if (diag == null) {
                Text("Appuyez sur « Vérifier serveur » pour interroger JARVIS.")
            } else if (diag.error != null) {
                Text("Erreur : ${diag.error}")
            } else {
                Text("Appareil : ${diag.deviceId ?: "—"}")
                Text("Points reçus (24 h) : ${diag.pointsReceived24h ?: 0}")
                Text("Dernier point serveur : ${diag.lastPointReceivedAt ?: "—"}")
            }
            JarvisSecondaryButton(
                text = if (state.isFetchingServerDiag) "Interrogation…" else "Vérifier serveur",
                onClick = { viewModel.fetchServerDiagnostics() },
                enabled = !state.isFetchingServerDiag,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        JarvisGlassCard(title = "Timeline") {
            if (state.timeline.isEmpty()) {
                Text("Aucun événement récent.")
            } else {
                state.timeline.forEach { entry ->
                    JarvisListItem(
                        title = entry.label,
                        subtitle = entry.timeLabel,
                        trailing = {
                            JarvisStatusBadge(
                                label = timelineBadge(entry.label),
                                tone = timelineTone(entry.label),
                            )
                        },
                    )
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            JarvisPrimaryButton(
                text = if (state.isSyncing) "Synchronisation…" else "Synchroniser",
                onClick = { viewModel.syncNow() },
                loading = state.isSyncing,
                enabled = !state.isSyncing,
                modifier = Modifier.weight(1f),
            )
            JarvisSecondaryButton(
                text = "Permissions",
                onClick = { openAppSettings(context) },
                modifier = Modifier.weight(1f),
            )
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            JarvisSecondaryButton(
                text = "Vider la file",
                onClick = { viewModel.requestClearPendingConfirm() },
                modifier = Modifier.weight(1f),
            )
            JarvisSecondaryButton(
                text = "Supprimer invalides",
                onClick = { viewModel.requestClearInvalidConfirm() },
                enabled = state.invalidCount > 0,
                modifier = Modifier.weight(1f),
            )
        }

        // TODO(JARVIS-FUTURE-LIVE-MAP): brancher la carte live quand l'API mobile sera validée.
        JarvisComingSoonCard(
            title = "Carte live",
            description = if (JarvisFeatureFlags.LIVE_MAP) {
                "Activation en cours — disponibilité côté mobile en validation."
            } else {
                "Affichage live des zones visitées bientôt disponible."
            },
        )

        // TODO(JARVIS-FUTURE-TRIPS-HISTORY): brancher l'historique de trajets détaillé.
        JarvisComingSoonCard(
            title = "Historique des trajets",
            description = if (JarvisFeatureFlags.TRIPS_HISTORY) {
                "Activation en cours — consolidation des trajets en préparation."
            } else {
                "Consulte bientôt les trajets consolidés et leur durée."
            },
        )

        state.message?.let { msg ->
            JarvisStatusBadge(
                label = msg,
                tone = if (msg.contains("expirée") || msg.contains("erreur", ignoreCase = true)) {
                    StatusTone.Danger
                } else {
                    StatusTone.Info
                },
            )
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

    if (state.showClearInvalidConfirm) {
        AlertDialog(
            onDismissRequest = { viewModel.dismissClearInvalidConfirm() },
            title = { Text("Supprimer les points invalides ?") },
            text = { Text("Les points marqués invalides seront supprimés définitivement.") },
            confirmButton = {
                TextButton(onClick = { viewModel.clearInvalid() }) {
                    Text("Confirmer")
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.dismissClearInvalidConfirm() }) {
                    Text("Annuler")
                }
            },
        )
    }
}

private fun openAppSettings(context: android.content.Context) {
    val intent = Intent(
        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        Uri.parse("package:${context.packageName}"),
    )
    context.startActivity(intent)
}

private fun timelineTone(label: String): StatusTone {
    val normalized = label.lowercase()
    return when {
        normalized.contains("échec") -> StatusTone.Danger
        normalized.contains("confirm") || normalized.contains("captur") -> StatusTone.Positive
        normalized.contains("envoy") || normalized.contains("batch") -> StatusTone.Info
        else -> StatusTone.Neutral
    }
}

private fun timelineBadge(label: String): String {
    val normalized = label.lowercase()
    return when {
        normalized.contains("échec") -> "Échec"
        normalized.contains("confirm") -> "Confirmé"
        normalized.contains("captur") -> "Capture"
        normalized.contains("envoy") -> "Envoi"
        normalized.contains("batch") -> "Batch"
        else -> "Info"
    }
}

private fun formatTs(epochMs: Long): String =
    if (epochMs <= 0L) {
        "—"
    } else {
        val ageSec = ((System.currentTimeMillis() - epochMs) / 1000L).coerceAtLeast(0)
        "il y a ${ageSec}s"
    }
