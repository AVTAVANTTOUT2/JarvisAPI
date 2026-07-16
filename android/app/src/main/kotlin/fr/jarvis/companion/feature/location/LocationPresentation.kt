package fr.jarvis.companion.feature.location

import fr.jarvis.companion.core.connectivity.ConnectivityState
import fr.jarvis.companion.core.ui.components.StatusTone

enum class LocationHealthLevel {
    Healthy,
    Attention,
    Problem,
}

data class LocationHeroVerdict(
    val level: LocationHealthLevel,
    val title: String,
    val detail: String,
    val badgeLabel: String,
)

fun deriveLocationHeroVerdict(state: LocationUiState): LocationHeroVerdict {
    if (!state.collectionEnabled) {
        return LocationHeroVerdict(
            level = LocationHealthLevel.Problem,
            title = "Collecte désactivée",
            detail = "Active la collecte pour reprendre les captures GPS.",
            badgeLabel = "Action requise",
        )
    }
    if (!state.finePermission) {
        return LocationHeroVerdict(
            level = LocationHealthLevel.Problem,
            title = "Permission GPS refusée",
            detail = "Permission de localisation nécessaire pour collecter les points.",
            badgeLabel = "Permission requise",
        )
    }
    if (state.connectivity == ConnectivityState.Unauthorized) {
        return LocationHeroVerdict(
            level = LocationHealthLevel.Problem,
            title = "Session expirée",
            detail = "Jeton révoqué ou expiré. Réappairage requis avant la sync.",
            badgeLabel = "Jeton révoqué",
        )
    }
    if (state.connectivity == ConnectivityState.Offline) {
        return LocationHeroVerdict(
            level = LocationHealthLevel.Attention,
            title = "Collecte active, sync en pause",
            detail = "Hors ligne pour le moment. Les points restent en file locale.",
            badgeLabel = "Hors ligne",
        )
    }
    if (state.failedCount > 0 || state.pendingCount >= 20 || state.lastCaptureTime == null) {
        return LocationHeroVerdict(
            level = LocationHealthLevel.Attention,
            title = "Localisation à surveiller",
            detail = "La collecte fonctionne mais certains signaux nécessitent une vérification.",
            badgeLabel = "Surveillance",
        )
    }
    return LocationHeroVerdict(
        level = LocationHealthLevel.Healthy,
        title = "La localisation fonctionne",
        detail = "Collecte, permissions et synchronisation sont opérationnelles.",
        badgeLabel = "Opérationnel",
    )
}

fun locationHealthTone(level: LocationHealthLevel): StatusTone = when (level) {
    LocationHealthLevel.Healthy -> StatusTone.Positive
    LocationHealthLevel.Attention -> StatusTone.Warning
    LocationHealthLevel.Problem -> StatusTone.Danger
}

fun formatLastCaptureSummary(lastCaptureTime: String?, lastCaptureAccuracy: String?): String {
    if (lastCaptureTime.isNullOrBlank()) {
        return "Aucune capture récente"
    }
    return if (lastCaptureAccuracy.isNullOrBlank()) {
        lastCaptureTime
    } else {
        "$lastCaptureTime ($lastCaptureAccuracy)"
    }
}

fun sanitizeTimelineLabel(label: String): String {
    val masked = PRECISE_COORDINATE_REGEX.replace(label) { "[coordonnée masquée]" }
    return masked.replace(Regex("\\s+"), " ").trim()
}

private val PRECISE_COORDINATE_REGEX = Regex("(?<!\\d)([-+]?\\d{1,3}\\.\\d{4,})(?!\\d)")
