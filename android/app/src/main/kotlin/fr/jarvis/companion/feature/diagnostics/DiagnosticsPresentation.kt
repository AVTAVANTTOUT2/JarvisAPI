package fr.jarvis.companion.feature.diagnostics

import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.ui.components.StatusTone
import java.net.URI

enum class DiagnosticsLevel {
    Ok,
    Attention,
    Problem,
}

data class DiagnosticsLine(
    val label: String,
    val value: String,
)

data class DiagnosticsSectionStatus(
    val title: String,
    val level: DiagnosticsLevel,
    val badgeLabel: String = "",
    val summary: String = "",
    val lines: List<DiagnosticsLine> = emptyList(),
)

data class DiagnosticsGlobalVerdict(
    val level: DiagnosticsLevel,
    val title: String,
    val detail: String,
)

fun computeGlobalDiagnosticsVerdict(sections: List<DiagnosticsSectionStatus>): DiagnosticsGlobalVerdict {
    val level = when {
        sections.any { it.level == DiagnosticsLevel.Problem } -> DiagnosticsLevel.Problem
        sections.any { it.level == DiagnosticsLevel.Attention } -> DiagnosticsLevel.Attention
        else -> DiagnosticsLevel.Ok
    }
    return when (level) {
        DiagnosticsLevel.Ok -> DiagnosticsGlobalVerdict(
            level = DiagnosticsLevel.Ok,
            title = "Système opérationnel",
            detail = "Tous les services critiques sont disponibles.",
        )
        DiagnosticsLevel.Attention -> DiagnosticsGlobalVerdict(
            level = DiagnosticsLevel.Attention,
            title = "Surveillance recommandée",
            detail = "Aucun blocage majeur, mais au moins un signal demande une vérification.",
        )
        DiagnosticsLevel.Problem -> DiagnosticsGlobalVerdict(
            level = DiagnosticsLevel.Problem,
            title = "Action requise",
            detail = "Un service critique est dégradé. Intervention recommandée.",
        )
    }
}

fun diagnosticsTone(level: DiagnosticsLevel): StatusTone = when (level) {
    DiagnosticsLevel.Ok -> StatusTone.Positive
    DiagnosticsLevel.Attention -> StatusTone.Warning
    DiagnosticsLevel.Problem -> StatusTone.Danger
}

fun evaluateConnectionStatus(
    connectivity: ConnectivityState,
    tokenPresent: Boolean,
    onboardingComplete: Boolean,
    serverConfigured: Boolean,
): DiagnosticsSectionStatus {
    val level = when {
        !serverConfigured || !tokenPresent || connectivity == ConnectivityState.Unauthorized -> DiagnosticsLevel.Problem
        connectivity == ConnectivityState.Offline || connectivity == ConnectivityState.NetworkAvailable || !onboardingComplete -> DiagnosticsLevel.Attention
        else -> DiagnosticsLevel.Ok
    }
    val badge = when {
        connectivity == ConnectivityState.Unauthorized -> "Jeton révoqué"
        !tokenPresent -> "Jeton absent"
        !serverConfigured -> "Serveur manquant"
        connectivity == ConnectivityState.Offline -> "Hors ligne"
        connectivity == ConnectivityState.NetworkAvailable -> "Réseau partiel"
        !onboardingComplete -> "Onboarding en cours"
        else -> "Connecté"
    }
    val summary = when {
        connectivity == ConnectivityState.Unauthorized -> "Réappairage requis pour rétablir la session."
        !tokenPresent -> "Aucun jeton natif stocké sur l'appareil."
        !serverConfigured -> "URL serveur non configurée."
        connectivity == ConnectivityState.Offline -> "Le réseau est indisponible."
        connectivity == ConnectivityState.NetworkAvailable -> "Le réseau est présent mais le backend reste à confirmer."
        !onboardingComplete -> "L'onboarding n'est pas terminé."
        else -> "Connexion backend valide."
    }
    return DiagnosticsSectionStatus(
        title = "Connexion",
        level = level,
        badgeLabel = badge,
        summary = summary,
    )
}

fun sanitizeDiagnosticValue(raw: String): String {
    var sanitized = raw
        .replace(TOKEN_QUERY_REGEX, "$1=[secret masqué]")
        .replace(BEARER_TOKEN_REGEX, "Bearer [secret masqué]")
        .replace(PRECISE_COORDINATE_REGEX, "[coordonnée masquée]")
        .replace(Regex("\\s+"), " ")
        .trim()
    if (sanitized.length > MAX_DIAGNOSTIC_VALUE_LENGTH) {
        sanitized = sanitized.take(MAX_DIAGNOSTIC_VALUE_LENGTH) + "…"
    }
    return sanitized
}

fun maskServerHost(serverUrl: String): String {
    if (serverUrl.isBlank()) return "(non configuré)"
    return try {
        val parsed = URI(serverUrl.trim())
        val host = parsed.host ?: return "(invalide)"
        val port = parsed.port
        if (port > 0) "$host:$port" else host
    } catch (_: Exception) {
        "(invalide)"
    }
}

fun maskDeviceId(deviceId: String): String {
    if (deviceId.isBlank()) return "android-***"
    val suffix = deviceId.takeLast(4)
    return "android-***$suffix"
}

fun buildRawDiagnosticsReport(
    globalVerdict: DiagnosticsGlobalVerdict,
    sections: List<DiagnosticsSectionStatus>,
): String = buildString {
    appendLine("JARVIS Companion Diagnostics")
    appendLine("global=${globalVerdict.level.name} title=${globalVerdict.title}")
    sections.forEach { section ->
        appendLine(
            "[${section.title}] level=${section.level.name} " +
                "badge=${section.badgeLabel} summary=${sanitizeDiagnosticValue(section.summary)}",
        )
        section.lines.forEach { line ->
            appendLine("${line.label}=${sanitizeDiagnosticValue(line.value)}")
        }
    }
}

private const val MAX_DIAGNOSTIC_VALUE_LENGTH = 180
private val BEARER_TOKEN_REGEX = Regex("(?i)bearer\\s+[a-z0-9._\\-~+/]+=*")
private val TOKEN_QUERY_REGEX = Regex("(?i)(token|api[_-]?key|secret|authorization)=([^\\s&]+)")
private val PRECISE_COORDINATE_REGEX = Regex("(?<!\\d)([-+]?\\d{1,3}\\.\\d{4,})(?!\\d)")
