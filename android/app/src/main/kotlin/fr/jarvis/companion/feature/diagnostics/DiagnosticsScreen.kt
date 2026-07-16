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
import fr.jarvis.companion.core.ui.components.JarvisCard
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.data.JarvisSettings
import java.net.URI

@Composable
fun DiagnosticsScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val container = context.appContainer()

    val connectivity by container.connectivityObserver.state.collectAsState()
    val syncMeta by container.database.syncMetadataDao().observeAll().collectAsState(initial = emptyList())
    val pendingCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.PENDING)
        .collectAsState(initial = 0)

    val report = remember(connectivity, syncMeta, pendingCount) {
        buildReport(context, connectivity, syncMeta, pendingCount)
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionHeader("Diagnostics", "Sans secrets")

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
            DiagnosticLine("Positions en attente", pendingCount.toString())
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

private fun buildReport(
    context: Context,
    connectivity: ConnectivityState,
    syncMeta: List<fr.jarvis.companion.core.database.SyncMetadataEntity>,
    pendingCount: Int,
): String = buildString {
    appendLine("JARVIS Companion Diagnostics")
    appendLine("version=${BuildConfig.VERSION_NAME} code=${BuildConfig.VERSION_CODE}")
    appendLine("device=${JarvisSettings.deviceId(context)}")
    appendLine("server=${maskServerHost(JarvisSettings.server(context))}")
    appendLine("token_present=${JarvisSettings.nativeToken(context).isNotEmpty()}")
    appendLine("connectivity=${connectivity.name}")
    appendLine("onboarding=${JarvisSettings.isOnboardingComplete(context)}")
    appendLine("pending_locations=$pendingCount")
    syncMeta.forEach { meta ->
        appendLine("sync.${meta.key}=${meta.lastError ?: meta.lastSuccessAtMillis}")
    }
}

private fun copyReport(context: Context, report: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("JARVIS Diagnostics", report))
}
