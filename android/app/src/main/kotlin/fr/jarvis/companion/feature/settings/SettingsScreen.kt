package fr.jarvis.companion.feature.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import fr.jarvis.companion.network.AgenticRunDto
import fr.jarvis.companion.network.AgenticRuntimeStatusDto
import fr.jarvis.companion.network.AgenticApprovalDto
import fr.jarvis.companion.network.AgenticArtifactDto
import fr.jarvis.companion.network.AgenticEventDto
import fr.jarvis.companion.network.JarvisApiResult
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    locationEnabled: Boolean,
    wakeEnabled: Boolean,
    hasPorcupineKey: Boolean,
    onLocationToggle: (Boolean) -> Unit,
    onWakeToggle: (Boolean) -> Unit,
    onPorcupineKeySave: (String) -> Unit,
    onOpenTasks: () -> Unit = {},
    onOpenConversations: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val container = remember(context) { context.appContainer() }
    val repository = container.repository
    val agenticWebSocket = container.chatWebSocket
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf(JarvisSettings.server(context)) }
    var serverFeedback by remember { mutableStateOf<String?>(null) }
    var serverErrorMessage by remember { mutableStateOf<String?>(null) }
    var porcupineKey by remember { mutableStateOf("") }
    var keyErrorMessage by remember { mutableStateOf<String?>(null) }
    var voiceFeedback by remember { mutableStateOf<String?>(null) }
    var agenticRuntime by remember { mutableStateOf<AgenticRuntimeStatusDto?>(null) }
    var agenticRuns by remember { mutableStateOf<List<AgenticRunDto>>(emptyList()) }
    var agenticStatusMessage by remember { mutableStateOf<String?>(null) }
    var selectedAgenticRunId by remember { mutableStateOf<String?>(null) }
    var selectedAgenticRun by remember { mutableStateOf<AgenticRunDto?>(null) }
    var agenticEvents by remember { mutableStateOf<List<AgenticEventDto>>(emptyList()) }
    var agenticApprovals by remember { mutableStateOf<List<AgenticApprovalDto>>(emptyList()) }
    var agenticArtifacts by remember { mutableStateOf<List<AgenticArtifactDto>>(emptyList()) }
    var agenticDetailLoading by remember { mutableStateOf(false) }
    var agenticActionInFlight by remember { mutableStateOf(false) }
    var agenticDetailMessage by remember { mutableStateOf<String?>(null) }
    var agenticRefreshToken by remember { mutableStateOf(0) }
    val futureOptions = remember { buildFutureSettingsOptions() }

    DisposableEffect(agenticWebSocket) {
        agenticWebSocket.connect()
        onDispose { agenticWebSocket.disconnect() }
    }

    LaunchedEffect(Unit) {
        val runtimeResult = repository.getAgenticRuntimeStatus()
        agenticRuntime = runtimeResult.value
        agenticStatusMessage = runtimeResult.error.takeIf { !runtimeResult.ok && it.isNotBlank() }
        val runsResult = repository.getAgenticRuns(limit = 5)
        if (runsResult.ok) {
            agenticRuns = runsResult.value.orEmpty()
            selectedAgenticRunId = agenticRuns.firstOrNull()?.stableId
        }
    }

    LaunchedEffect(selectedAgenticRunId, agenticRefreshToken) {
        val runId = selectedAgenticRunId ?: return@LaunchedEffect
        agenticDetailLoading = true
        val runResult = repository.getAgenticRun(runId)
        val eventsResult = repository.getAgenticRunEvents(runId)
        val approvalsResult = repository.getAgenticRunApprovals(runId)
        val artifactsResult = repository.getAgenticRunArtifacts(runId)
        selectedAgenticRun = runResult.value
            ?: selectedAgenticRun
            ?: agenticRuns.firstOrNull { it.stableId == runId }
        runResult.value?.let { refreshed ->
            agenticRuns = agenticRuns.map { if (it.stableId == refreshed.stableId) refreshed else it }
        }
        agenticEvents = eventsResult.value.orEmpty()
        agenticApprovals = approvalsResult.value.orEmpty()
        agenticArtifacts = artifactsResult.value.orEmpty()
        val failure = listOf(runResult, eventsResult, approvalsResult, artifactsResult)
            .firstOrNull { !it.ok }
        failure?.error?.let {
            agenticDetailMessage =
                AgenticDisplayPolicy.safeText(it, "Détail temporairement indisponible")
        }
        agenticDetailLoading = false
    }

    LaunchedEffect(agenticWebSocket) {
        agenticWebSocket.agenticEvents.collectLatest { event ->
            val selectedBeforeRefresh = selectedAgenticRunId
            val plan = event.refreshPlan(selectedBeforeRefresh)
            if (plan.refreshRuns) {
                val runsResult = repository.getAgenticRuns(limit = 5)
                if (runsResult.ok) {
                    agenticRuns = runsResult.value.orEmpty()
                    if (selectedBeforeRefresh == null) {
                        selectedAgenticRunId = agenticRuns
                            .firstOrNull { it.stableId == event.run_id }
                            ?.stableId
                            ?: agenticRuns.firstOrNull()?.stableId
                    }
                }
            }
            if (
                plan.refreshDetails || plan.refreshApprovals || plan.refreshArtifacts
            ) {
                agenticRefreshToken += 1
            }
        }
    }

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

        JarvisCard(title = "Activité agentique") {
            val runtimeAvailable = agenticRuntime?.isAvailable == true
            JarvisStatusBadge(
                if (runtimeAvailable) "Moteur disponible" else "Moteur indisponible",
                tone = if (runtimeAvailable) StatusTone.Positive else StatusTone.Warning,
            )
            Text(
                "${agenticRuntime?.active_runs ?: 0} active(s) · ${agenticRuntime?.queued_runs ?: 0} en file",
                style = MaterialTheme.typography.bodyMedium,
            )
            agenticStatusMessage?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
            if (agenticRuns.isEmpty()) {
                Text("Aucune tâche récente.", style = MaterialTheme.typography.bodySmall)
            } else {
                agenticRuns.take(3).forEach { run ->
                    OutlinedButton(
                        onClick = {
                            agenticDetailMessage = null
                            selectedAgenticRunId = run.stableId
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(AgenticDisplayPolicy.safeText(run.title), maxLines = 1)
                            Text(
                                run.phase ?: run.status,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (run.requires_attention) {
                            JarvisStatusBadge("Action requise", tone = StatusTone.Warning)
                        }
                    }
                }
                selectedAgenticRun?.let { run ->
                    Spacer(Modifier.height(8.dp))
                    AgenticRunDetail(
                        run = run,
                        events = agenticEvents,
                        approvals = agenticApprovals,
                        artifacts = agenticArtifacts,
                        isLoading = agenticDetailLoading,
                        actionInFlight = agenticActionInFlight,
                        message = agenticDetailMessage,
                        onPause = {
                            scope.launch {
                                agenticActionInFlight = true
                                val result = repository.pauseAgenticRun(run.stableId)
                                agenticDetailMessage = result.agenticActionMessage("Tâche mise en pause.")
                                if (result.ok) agenticRefreshToken += 1
                                agenticActionInFlight = false
                            }
                        },
                        onResume = {
                            scope.launch {
                                agenticActionInFlight = true
                                val result = repository.resumeAgenticRun(run.stableId)
                                agenticDetailMessage = result.agenticActionMessage("Tâche reprise.")
                                if (result.ok) agenticRefreshToken += 1
                                agenticActionInFlight = false
                            }
                        },
                        onCancel = {
                            scope.launch {
                                agenticActionInFlight = true
                                val result = repository.cancelAgenticRun(run.stableId)
                                agenticDetailMessage = result.agenticActionMessage("Annulation demandée.")
                                if (result.ok) agenticRefreshToken += 1
                                agenticActionInFlight = false
                            }
                        },
                        onApprovalDecision = { approval, approved ->
                            scope.launch {
                                agenticActionInFlight = true
                                val result = repository.decideAgenticApproval(
                                    runId = run.stableId,
                                    approvalId = approval.stableId,
                                    approved = approved,
                                )
                                agenticDetailMessage = result.agenticActionMessage("Décision enregistrée.")
                                if (result.ok) agenticRefreshToken += 1
                                agenticActionInFlight = false
                            }
                        },
                        onOpenTask = onOpenTasks,
                        onOpenConversation = onOpenConversations,
                    )
                }
            }
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
private fun AgenticRunDetail(
    run: AgenticRunDto,
    events: List<AgenticEventDto>,
    approvals: List<AgenticApprovalDto>,
    artifacts: List<AgenticArtifactDto>,
    isLoading: Boolean,
    actionInFlight: Boolean,
    message: String?,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
    onApprovalDecision: (AgenticApprovalDto, Boolean) -> Unit,
    onOpenTask: () -> Unit,
    onOpenConversation: () -> Unit,
) {
    val status = run.status.lowercase()
    val visibleApprovals = approvals.ifEmpty { run.approvals }
    val visibleArtifacts = artifacts.ifEmpty { run.artifacts }
    val plan = AgenticDisplayPolicy.planItems(run, events)
    val result = AgenticDisplayPolicy.resultSummary(run)

    Column(modifier = Modifier.fillMaxWidth()) {
        Text("Détail de la tâche", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text(
            AgenticDisplayPolicy.safeText(run.title),
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            "${run.phase ?: run.status} · ${AgenticDisplayPolicy.durationLabel(run)}",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        run.progressPercent?.let { Text("Progression : $it %", style = MaterialTheme.typography.bodySmall) }
        run.summary?.let {
            Text(AgenticDisplayPolicy.safeText(it), style = MaterialTheme.typography.bodySmall)
        }
        if (isLoading) {
            Text("Actualisation du détail…", style = MaterialTheme.typography.bodySmall)
        }
        message?.let {
            Text(
                AgenticDisplayPolicy.safeText(it),
                style = MaterialTheme.typography.bodySmall,
                color = if (it.contains("indisponible", ignoreCase = true)) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
        }

        if (!run.task_id.isNullOrBlank() || !run.conversation_id.isNullOrBlank()) {
            AgenticDetailHeading("Liens")
            run.task_id?.takeIf(String::isNotBlank)?.let {
                OutlinedButton(onClick = onOpenTask, modifier = Modifier.fillMaxWidth()) {
                    Text("Ouvrir la tâche liée")
                }
            }
            run.conversation_id?.takeIf(String::isNotBlank)?.let {
                OutlinedButton(onClick = onOpenConversation, modifier = Modifier.fillMaxWidth()) {
                    Text("Ouvrir la conversation liée")
                }
            }
        }

        AgenticDetailHeading("Plan")
        if (plan.isEmpty()) {
            Text("Plan non communiqué.", style = MaterialTheme.typography.bodySmall)
        } else {
            plan.forEachIndexed { index, item ->
                Text("${index + 1}. $item", style = MaterialTheme.typography.bodySmall)
            }
        }

        AgenticDetailHeading("Étapes")
        if (run.steps.isEmpty()) {
            Text("Les étapes sont consignées dans l’activité.", style = MaterialTheme.typography.bodySmall)
        } else {
            run.steps.take(20).forEach { step ->
                val label = AgenticDisplayPolicy.safeText(step.title ?: step.name, "Étape")
                Text("• $label — ${step.status}", style = MaterialTheme.typography.bodySmall)
                step.summary?.let {
                    Text(
                        AgenticDisplayPolicy.safeText(it),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        AgenticDetailHeading("Activité")
        if (events.isEmpty()) {
            Text("Aucune activité publiée.", style = MaterialTheme.typography.bodySmall)
        } else {
            events.sortedBy { it.sequence }.takeLast(12).forEach { event ->
                Text("• ${AgenticDisplayPolicy.eventLabel(event)}", style = MaterialTheme.typography.bodySmall)
            }
        }

        AgenticDetailHeading("Outils")
        val toolEvents = events.filter { it.type == "agent.tool.started" || it.type == "agent.tool.completed" }
        if (toolEvents.isEmpty()) {
            Text("Aucun outil signalé.", style = MaterialTheme.typography.bodySmall)
        } else {
            toolEvents.takeLast(8).forEach { event ->
                Text("• ${AgenticDisplayPolicy.eventLabel(event)}", style = MaterialTheme.typography.bodySmall)
            }
        }

        AgenticDetailHeading("Approbations")
        if (visibleApprovals.isEmpty()) {
            Text("Aucune approbation.", style = MaterialTheme.typography.bodySmall)
        } else {
            visibleApprovals.take(12).forEach { approval ->
                Text(
                    AgenticDisplayPolicy.safeText(approval.title ?: approval.action, "Autorisation"),
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                approval.summary?.let {
                    Text(AgenticDisplayPolicy.safeText(it), style = MaterialTheme.typography.bodySmall)
                }
                Text(
                    buildString {
                        append(approval.displayStatus)
                        if (approval.risks.isNotEmpty()) append(" · risque signalé")
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (approval.isPending) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = { onApprovalDecision(approval, true) },
                            enabled = !actionInFlight,
                        ) {
                            Text("Autoriser")
                        }
                        OutlinedButton(
                            onClick = { onApprovalDecision(approval, false) },
                            enabled = !actionInFlight,
                        ) {
                            Text("Refuser")
                        }
                    }
                }
            }
        }

        run.error?.let { error ->
            AgenticDetailHeading("Erreur")
            Text(
                AgenticDisplayPolicy.safeText(error.message, "Erreur sans détail exposable"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            error.code?.let {
                Text(AgenticDisplayPolicy.safeText(it), style = MaterialTheme.typography.labelSmall)
            }
        } ?: run.error_message?.let {
            AgenticDetailHeading("Erreur")
            Text(
                AgenticDisplayPolicy.safeText(it, "Erreur sans détail exposable"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }

        AgenticDetailHeading("Artefacts")
        if (visibleArtifacts.isEmpty()) {
            Text("Aucun artefact.", style = MaterialTheme.typography.bodySmall)
        } else {
            visibleArtifacts.take(20).forEach { artifact ->
                Text("• ${AgenticDisplayPolicy.artifactLabel(artifact)}", style = MaterialTheme.typography.bodySmall)
            }
            Text(
                "Les références et chemins restent masqués.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        AgenticDetailHeading("Résultat")
        Text(result ?: "Aucun résultat final.", style = MaterialTheme.typography.bodySmall)

        AgenticDetailHeading("Contrôles")
        if (status in setOf("planning", "running", "executing", "verifying")) {
            OutlinedButton(
                onClick = onPause,
                enabled = !actionInFlight,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Mettre en pause")
            }
        }
        if (status == "paused") {
            OutlinedButton(
                onClick = onResume,
                enabled = !actionInFlight,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Reprendre")
            }
        }
        if (status !in setOf("completed", "failed", "cancelled")) {
            OutlinedButton(
                onClick = onCancel,
                enabled = !actionInFlight,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Annuler la tâche")
            }
        }
    }
}

@Composable
private fun AgenticDetailHeading(title: String) {
    Spacer(Modifier.height(10.dp))
    Text(title, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
}

private fun JarvisApiResult.agenticActionMessage(success: String): String =
    if (ok) success else AgenticDisplayPolicy.safeText(error, "Action impossible")

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
