package fr.jarvis.companion.feature.diagnostics

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.location.LocationConstants
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings
import kotlinx.coroutines.runBlocking
import java.net.URI
import java.util.concurrent.TimeUnit

@Composable
fun DiagnosticsScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val container = context.appContainer()

    val connectivity by container.connectivityObserver.state.collectAsState()
    val syncMeta by container.database.syncMetadataDao().observeAll().collectAsState(initial = emptyList())
    val pendingCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.PENDING)
        .collectAsState(initial = 0)
    val retryableCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.FAILED_RETRYABLE)
        .collectAsState(initial = 0)
    val invalidCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.INVALID)
        .collectAsState(initial = 0)

    val locationExtras = remember(pendingCount, retryableCount, invalidCount, syncMeta) {
        runBlocking {
            buildLocationDiagnostics(context, container, pendingCount, retryableCount, invalidCount, syncMeta)
        }
    }

    val report = remember(connectivity, syncMeta, pendingCount, retryableCount, locationExtras) {
        buildReport(context, connectivity, syncMeta, pendingCount + retryableCount, locationExtras)
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Diagnostics", "Sans secrets ni coordonnées")

        JarvisCard(title = "Application") {
            DiagnosticLine("Version", BuildConfig.VERSION_NAME)
            DiagnosticLine("Code", BuildConfig.VERSION_CODE.toString())
            DiagnosticLine("Device ID", JarvisSettings.deviceId(context))
        }

        JarvisCard(title = "Connexion") {
            DiagnosticLine("Serveur", maskServerHost(JarvisSettings.server(context)))
            DiagnosticLine("Token présent", if (JarvisSettings.nativeToken(context).isNotEmpty()) "oui" else "non")
            DiagnosticLine("État réseau", connectivity.name)
            DiagnosticLine("Onboarding", if (JarvisSettings.isOnboardingComplete(context)) "terminé" else "en cours")
        }

        JarvisCard(title = "Synchronisation") {
            if (syncMeta.isEmpty()) {
                Text("Aucune métadonnée de sync.")
            } else {
                syncMeta.forEach { meta ->
                    DiagnosticLine(
                        meta.key,
                        meta.lastError ?: meta.lastSuccessAtMillis?.toString() ?: "—",
                    )
                }
            }
            DiagnosticLine("Positions en attente", (pendingCount + retryableCount).toString())
        }

        JarvisCard(title = "Localisation GPS") {
            locationExtras.forEach { (label, value) ->
                DiagnosticLine(label, value)
            }
        }

        Button(
            onClick = { copyReport(context, report) },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Copier le rapport")
        }

        Text(
            report,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun DiagnosticLine(label: String, value: String) {
    Text("$label : $value", style = MaterialTheme.typography.bodyMedium)
}

private fun maskServerHost(serverUrl: String): String {
    if (serverUrl.isBlank()) return "(non configuré)"
    return try {
        val host = URI(serverUrl).host ?: "?"
        val port = URI(serverUrl).port
        if (port > 0) "$host:$port" else host
    } catch (_: Exception) {
        "(invalide)"
    }
}

private suspend fun buildLocationDiagnostics(
    context: Context,
    container: fr.jarvis.companion.app.AppContainer,
    pending: Int,
    retryable: Int,
    invalid: Int,
    syncMeta: List<fr.jarvis.companion.core.database.SyncMetadataEntity>,
): List<Pair<String, String>> {
    val store = container.pendingLocationStore
    val lock = container.database.locationSyncLockDao().getLock()
    val oldest = store.getOldestPendingCapturedAt()
    val lastSync = syncMeta.find { it.key == LocationConstants.META_LAST_SYNC_AT }?.lastSuccessAtMillis
    val lastBatch = syncMeta.find { it.key == LocationConstants.META_LAST_BATCH_SIZE }?.lastSuccessAtMillis
    val lastHttp = syncMeta.find { it.key == LocationConstants.META_LAST_HTTP_STATUS }?.lastSuccessAtMillis
    val totalRoom = container.database.pendingLocationDao().countAll()

    return listOf(
        "Service GPS" to if (JarvisSettings.isLocationEnabled(context)) "actif" else "inactif",
        "Pending" to (pending + retryable).toString(),
        "Invalides" to invalid.toString(),
        "Plus ancien pending" to (oldest?.let { formatRelative(it) } ?: "—"),
        "Taille Room GPS" to totalRoom.toString(),
        "Dernier lot" to (lastBatch?.toString() ?: "—"),
        "Dernier HTTP batch" to (lastHttp?.toString() ?: "—"),
        "Dernière sync" to formatRelativeFromNow(lastSync),
        "Verrou sync" to (if (lock?.lockedBy != null) "occupé" else "libre"),
        "Worker" to fr.jarvis.companion.core.sync.LocationSyncWorker.WORK_NAME,
        "Backend reachable" to container.connectivityObserver.state.value.let {
            if (it == ConnectivityState.ServerReachable) "oui" else "non"
        },
    )
}

private fun formatRelative(epochMs: Long): String {
    val diff = System.currentTimeMillis() - epochMs
    val days = TimeUnit.MILLISECONDS.toDays(diff)
    return when {
        days > 0 -> "il y a $days j"
        else -> {
            val hours = TimeUnit.MILLISECONDS.toHours(diff)
            if (hours > 0) "il y a $hours h" else "récent"
        }
    }
}

private fun formatRelativeFromNow(at: Long?): String {
    if (at == null) return "jamais"
    val minutes = TimeUnit.MILLISECONDS.toMinutes(System.currentTimeMillis() - at).coerceAtLeast(0)
    return when {
        minutes < 1 -> "à l'instant"
        minutes < 60 -> "il y a $minutes min"
        else -> "il y a ${minutes / 60} h"
    }
}

private fun buildReport(
    context: Context,
    connectivity: ConnectivityState,
    syncMeta: List<fr.jarvis.companion.core.database.SyncMetadataEntity>,
    pendingCount: Int,
    locationExtras: List<Pair<String, String>>,
): String = buildString {
    appendLine("JARVIS Companion Diagnostics")
    appendLine("version=${BuildConfig.VERSION_NAME} code=${BuildConfig.VERSION_CODE}")
    appendLine("device=${JarvisSettings.deviceId(context)}")
    appendLine("server=${maskServerHost(JarvisSettings.server(context))}")
    appendLine("token_present=${JarvisSettings.nativeToken(context).isNotEmpty()}")
    appendLine("connectivity=${connectivity.name}")
    appendLine("onboarding=${JarvisSettings.isOnboardingComplete(context)}")
    appendLine("pending_locations=$pendingCount")
    locationExtras.forEach { (k, v) -> appendLine("location.$k=$v") }
    syncMeta.forEach { meta ->
        appendLine("sync.${meta.key}=${meta.lastError ?: meta.lastSuccessAtMillis}")
    }
}

private fun copyReport(context: Context, report: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("JARVIS Diagnostics", report))
}
