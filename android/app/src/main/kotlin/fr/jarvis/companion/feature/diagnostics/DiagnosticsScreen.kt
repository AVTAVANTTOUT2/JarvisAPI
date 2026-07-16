package fr.jarvis.companion.feature.diagnostics

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.JarvisFeatureFlags
import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.database.CachedNotificationEntity
import fr.jarvis.companion.core.database.PendingLocationSyncState
import fr.jarvis.companion.core.location.LocationConstants
import fr.jarvis.companion.core.ui.components.GlassVariant
import fr.jarvis.companion.core.ui.components.JarvisComingSoonCard
import fr.jarvis.companion.core.ui.components.JarvisGlassCard
import fr.jarvis.companion.core.ui.components.JarvisListItem
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.core.ui.components.JarvisSecondaryButton
import fr.jarvis.companion.core.ui.components.SectionHeader
import fr.jarvis.companion.core.ui.components.JarvisStatusBadge
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.ui.theme.JarvisSpacing
import kotlinx.coroutines.delay
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
    val failedCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.FAILED_PERMANENT)
        .collectAsState(initial = 0)
    val invalidCount by container.database.pendingLocationDao()
        .observeCountByState(PendingLocationSyncState.INVALID)
        .collectAsState(initial = 0)
    val unreadNotifications by container.database.cachedNotificationDao()
        .observeUnread()
        .collectAsState(initial = emptyList())

    val asyncSnapshot by produceState(
        DiagnosticsAsyncSnapshot(),
        pendingCount,
        retryableCount,
        failedCount,
        invalidCount,
        syncMeta,
    ) {
        value = loadDiagnosticsAsyncSnapshot(container)
    }
    val pendingTotal = pendingCount + retryableCount
    val tokenPresent = JarvisSettings.nativeToken(context).isNotEmpty()
    val wakeWordEnabled = JarvisSettings.isWakeWordEnabled(context)
    val wakeKeyPresent = JarvisSettings.porcupineAccessKey(context).isNotBlank()
    val appSection = DiagnosticsSectionStatus(
        title = "Application",
        level = DiagnosticsLevel.Ok,
        badgeLabel = "OK",
        summary = "Build local valide et identifiant d'appareil masqué.",
        lines = listOf(
            DiagnosticsLine("Version", BuildConfig.VERSION_NAME),
            DiagnosticsLine("Code", BuildConfig.VERSION_CODE.toString()),
            DiagnosticsLine("Device ID", maskDeviceId(JarvisSettings.deviceId(context))),
        ),
    )
    val connectionSection = evaluateConnectionStatus(
        connectivity = connectivity,
        tokenPresent = tokenPresent,
        onboardingComplete = JarvisSettings.isOnboardingComplete(context),
        serverConfigured = JarvisSettings.hasServerConfigured(context),
    ).copy(
        lines = listOf(
            DiagnosticsLine("Serveur", maskServerHost(JarvisSettings.server(context))),
            DiagnosticsLine("Token présent", if (tokenPresent) "oui" else "non"),
            DiagnosticsLine("État réseau", connectivity.name),
            DiagnosticsLine(
                "Onboarding",
                if (JarvisSettings.isOnboardingComplete(context)) "terminé" else "en cours",
            ),
        ),
    )
    val syncSection = buildSynchronizationSection(
        connectivity = connectivity,
        pendingTotal = pendingTotal,
        failedCount = failedCount,
        invalidCount = invalidCount,
        syncMeta = syncMeta,
        lockOccupied = asyncSnapshot.lockOccupied,
    )
    val gpsSection = buildGpsSection(
        context = context,
        pendingTotal = pendingTotal,
        oldestPendingAt = asyncSnapshot.oldestPendingAt,
        roomCount = asyncSnapshot.roomCount,
    )
    val voiceSection = DiagnosticsSectionStatus(
        title = "Voix",
        level = when {
            wakeWordEnabled && !wakeKeyPresent -> DiagnosticsLevel.Attention
            else -> DiagnosticsLevel.Ok
        },
        badgeLabel = when {
            wakeWordEnabled && !wakeKeyPresent -> "Clé manquante"
            wakeWordEnabled -> "Wake word actif"
            else -> "Wake word inactif"
        },
        summary = when {
            wakeWordEnabled && !wakeKeyPresent -> "Le wake word est activé sans clé Porcupine."
            wakeWordEnabled -> "Pipeline vocal prêt sur cet appareil."
            else -> "Activation vocale désactivée dans les réglages."
        },
        lines = listOf(
            DiagnosticsLine("Wake word", if (wakeWordEnabled) "activé" else "désactivé"),
            DiagnosticsLine("Clé Porcupine", if (wakeKeyPresent) "présente" else "absente"),
        ),
    )
    val notificationsSection = buildNotificationsSection(unreadNotifications)
    val sections = remember(
        appSection,
        connectionSection,
        syncSection,
        gpsSection,
        voiceSection,
        notificationsSection,
    ) {
        listOf(appSection, connectionSection, syncSection, gpsSection, voiceSection, notificationsSection)
    }
    val globalVerdict = remember(sections) {
        computeGlobalDiagnosticsVerdict(sections)
    }
    val report = remember(globalVerdict, sections) {
        buildRawDiagnosticsReport(globalVerdict, sections)
    }
    var reportExpanded by rememberSaveable { mutableStateOf(false) }
    var reportCopied by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(reportCopied) {
        if (reportCopied) {
            delay(2_000)
            reportCopied = false
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(JarvisSpacing.lg),
        verticalArrangement = Arrangement.spacedBy(JarvisSpacing.lg),
    ) {
        SectionHeader("Diagnostics", "Sans secrets ni coordonnées")

        JarvisGlassCard(
            variant = when (globalVerdict.level) {
                DiagnosticsLevel.Ok -> GlassVariant.Accent
                DiagnosticsLevel.Attention -> GlassVariant.Default
                DiagnosticsLevel.Problem -> GlassVariant.Danger
            },
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(globalVerdict.title, style = MaterialTheme.typography.titleMedium)
                JarvisStatusBadge(
                    label = globalVerdict.level.name,
                    tone = diagnosticsTone(globalVerdict.level),
                )
            }
            Text(globalVerdict.detail, style = MaterialTheme.typography.bodyMedium)
        }

        sections.forEach { section ->
            DiagnosticsSectionCard(section = section)
        }

        if (!JarvisFeatureFlags.OFFLINE_DETAIL) {
            // TODO(JARVIS-FUTURE-OFFLINE-DETAIL): brancher la vue détaillée de la file
            // hors ligne (pending_location + pending_chat_operations) lorsque le flag
            // OFFLINE_DETAIL passera à true — métriques agrégées déjà disponibles ci-dessus.
            JarvisComingSoonCard(
                title = "File hors ligne détaillée",
                description = "Inspection point par point des files de synchronisation — bientôt disponible.",
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            JarvisPrimaryButton(
                text = "Copier le rapport",
                onClick = {
                    copyReport(context, report)
                    reportCopied = true
                },
                modifier = Modifier.weight(1f),
            )
            JarvisSecondaryButton(
                text = if (reportExpanded) "Masquer brut" else "Voir brut",
                onClick = { reportExpanded = !reportExpanded },
                modifier = Modifier.weight(1f),
            )
        }

        if (reportCopied) {
            JarvisStatusBadge(label = "Rapport copié", tone = diagnosticsTone(DiagnosticsLevel.Ok))
        }

        if (reportExpanded) {
            JarvisGlassCard(title = "Rapport brut (masqué)") {
                Text(
                    report,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                )
            }
        }
    }
}

@Composable
private fun DiagnosticsSectionCard(section: DiagnosticsSectionStatus) {
    JarvisGlassCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(section.title, style = MaterialTheme.typography.titleMedium)
            JarvisStatusBadge(
                label = section.badgeLabel,
                tone = diagnosticsTone(section.level),
            )
        }
        Text(
            section.summary,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        section.lines.forEach { line ->
            JarvisListItem(
                title = line.label,
                subtitle = sanitizeDiagnosticValue(line.value),
            )
        }
    }
}

private fun buildSynchronizationSection(
    connectivity: ConnectivityState,
    pendingTotal: Int,
    failedCount: Int,
    invalidCount: Int,
    syncMeta: List<fr.jarvis.companion.core.database.SyncMetadataEntity>,
    lockOccupied: Boolean,
): DiagnosticsSectionStatus {
    val lastSyncAt = syncMeta.find { it.key == LocationConstants.META_LAST_SYNC_AT }?.lastSuccessAtMillis
    val level = when {
        connectivity == ConnectivityState.Unauthorized -> DiagnosticsLevel.Problem
        failedCount > 0 -> DiagnosticsLevel.Problem
        pendingTotal >= 50 || invalidCount > 0 -> DiagnosticsLevel.Attention
        pendingTotal > 0 && lastSyncAt == null -> DiagnosticsLevel.Attention
        else -> DiagnosticsLevel.Ok
    }
    val badge = when (level) {
        DiagnosticsLevel.Ok -> "Stable"
        DiagnosticsLevel.Attention -> "Backlog"
        DiagnosticsLevel.Problem -> "Bloquée"
    }
    val summary = when (level) {
        DiagnosticsLevel.Ok -> "Synchronisation nominale."
        DiagnosticsLevel.Attention -> "Une vérification de la file locale est recommandée."
        DiagnosticsLevel.Problem -> "Des erreurs bloquantes empêchent une sync saine."
    }
    return DiagnosticsSectionStatus(
        title = "Synchronisation",
        level = level,
        badgeLabel = badge,
        summary = summary,
        lines = listOf(
            DiagnosticsLine("Positions en attente", pendingTotal.toString()),
            DiagnosticsLine("Échecs permanents", failedCount.toString()),
            DiagnosticsLine("Invalides", invalidCount.toString()),
            DiagnosticsLine("Dernière sync", formatRelativeFromNow(lastSyncAt)),
            DiagnosticsLine("Verrou sync", if (lockOccupied) "occupé" else "libre"),
        ),
    )
}

private fun buildGpsSection(
    context: Context,
    pendingTotal: Int,
    oldestPendingAt: Long?,
    roomCount: Int,
): DiagnosticsSectionStatus {
    val collectionEnabled = JarvisSettings.isLocationEnabled(context)
    val finePermission = hasFinePermission(context)
    val backgroundPermission = hasBackgroundPermission(context)
    val level = when {
        !collectionEnabled || !finePermission -> DiagnosticsLevel.Problem
        !backgroundPermission || pendingTotal >= 100 -> DiagnosticsLevel.Attention
        else -> DiagnosticsLevel.Ok
    }
    val badge = when (level) {
        DiagnosticsLevel.Ok -> "Collecte OK"
        DiagnosticsLevel.Attention -> "À surveiller"
        DiagnosticsLevel.Problem -> "Permission requise"
    }
    val summary = when (level) {
        DiagnosticsLevel.Ok -> "Collecte active avec permissions nécessaires."
        DiagnosticsLevel.Attention -> "Collecte active, mais certains paramètres sont incomplets."
        DiagnosticsLevel.Problem -> "Collecte indisponible tant que les permissions ne sont pas accordées."
    }
    return DiagnosticsSectionStatus(
        title = "GPS",
        level = level,
        badgeLabel = badge,
        summary = summary,
        lines = listOf(
            DiagnosticsLine("Collecte", if (collectionEnabled) "activée" else "désactivée"),
            DiagnosticsLine("Permission fine", if (finePermission) "accordée" else "refusée"),
            DiagnosticsLine("Permission arrière-plan", if (backgroundPermission) "accordée" else "refusée"),
            DiagnosticsLine("Plus ancien pending", oldestPendingAt?.let(::formatRelative) ?: "—"),
            DiagnosticsLine("Taille file locale", roomCount.toString()),
        ),
    )
}

private fun buildNotificationsSection(
    unreadNotifications: List<CachedNotificationEntity>,
): DiagnosticsSectionStatus {
    val unreadCount = unreadNotifications.size
    val urgentCount = unreadNotifications.count { it.priority.equals("urgent", ignoreCase = true) }
    val level = when {
        urgentCount > 0 -> DiagnosticsLevel.Attention
        unreadCount > 20 -> DiagnosticsLevel.Attention
        else -> DiagnosticsLevel.Ok
    }
    val badge = when {
        urgentCount > 0 -> "Urgent"
        unreadCount > 0 -> "En attente"
        else -> "Calme"
    }
    val summary = when {
        urgentCount > 0 -> "Des notifications urgentes attendent une action."
        unreadCount > 0 -> "Des notifications non lues sont en attente."
        else -> "Aucune notification critique."
    }
    return DiagnosticsSectionStatus(
        title = "Notifications",
        level = level,
        badgeLabel = badge,
        summary = summary,
        lines = listOf(
            DiagnosticsLine("Non lues", unreadCount.toString()),
            DiagnosticsLine("Urgentes", urgentCount.toString()),
        ),
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

private suspend fun loadDiagnosticsAsyncSnapshot(
    container: fr.jarvis.companion.app.AppContainer,
): DiagnosticsAsyncSnapshot {
    val oldestPendingAt = runCatching { container.pendingLocationStore.getOldestPendingCapturedAt() }
        .getOrNull()
    val roomCount = runCatching { container.database.pendingLocationDao().countAll() }
        .getOrDefault(0)
    val lockOccupied = runCatching { container.database.locationSyncLockDao().getLock()?.lockedBy != null }
        .getOrDefault(false)
    return DiagnosticsAsyncSnapshot(
        oldestPendingAt = oldestPendingAt,
        roomCount = roomCount,
        lockOccupied = lockOccupied,
    )
}

private fun copyReport(context: Context, report: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("JARVIS Diagnostics", report))
}

private fun hasFinePermission(context: Context): Boolean =
    context.checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) ==
        PackageManager.PERMISSION_GRANTED

private fun hasBackgroundPermission(context: Context): Boolean {
    return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        context.checkSelfPermission(android.Manifest.permission.ACCESS_BACKGROUND_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
    } else {
        hasFinePermission(context)
    }
}

private data class DiagnosticsAsyncSnapshot(
    val oldestPendingAt: Long? = null,
    val roomCount: Int = 0,
    val lockOccupied: Boolean = false,
)
