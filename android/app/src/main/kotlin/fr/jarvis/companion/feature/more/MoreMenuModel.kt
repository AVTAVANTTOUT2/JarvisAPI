package fr.jarvis.companion.feature.more

import fr.jarvis.companion.navigation.JarvisDestination

enum class MoreTileKind {
    RealRoute,
    FuturePlaceholder,
}

data class MoreTileModel(
    val title: String,
    val subtitle: String,
    val kind: MoreTileKind,
    val route: String?,
    val futureFlagId: String?,
)

fun MoreTileModel.isNavigable(): Boolean = kind == MoreTileKind.RealRoute && !route.isNullOrBlank()

fun MoreTileModel.toAccessibilityHint(): String = if (isNavigable()) {
    "$title, ouvrir"
} else {
    "$title, bientôt disponible"
}

fun buildMoreMenuTiles(): List<MoreTileModel> = listOf(
    MoreTileModel(
        title = "Tâches",
        subtitle = "Liste complète et priorités",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.TASKS,
        futureFlagId = null,
    ),
    MoreTileModel(
        title = "Localisation",
        subtitle = "Présence GPS et synchronisation",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.LOCATION,
        futureFlagId = null,
    ),
    MoreTileModel(
        title = "Notifications",
        subtitle = "Alertes JARVIS",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.NOTIFICATIONS,
        futureFlagId = null,
    ),
    MoreTileModel(
        title = "Diagnostics",
        subtitle = "État technique local",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.DIAGNOSTICS,
        futureFlagId = null,
    ),
    MoreTileModel(
        title = "Réglages",
        subtitle = "Connexion, voix et sécurité",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.SETTINGS,
        futureFlagId = null,
    ),
    MoreTileModel(
        title = "Réparation",
        subtitle = "Réappairage et récupération",
        kind = MoreTileKind.RealRoute,
        route = JarvisDestination.REPAIR,
        futureFlagId = null,
    ),
    // TODO(JARVIS-FUTURE-MEMORY-VIEW): activer la vue mémoire mobile.
    MoreTileModel(
        title = "Mémoire",
        subtitle = "Vue personnelle JARVIS",
        kind = MoreTileKind.FuturePlaceholder,
        route = null,
        futureFlagId = "JARVIS-FUTURE-MEMORY-VIEW",
    ),
    // TODO(JARVIS-FUTURE-CONTACTS): activer la vue Contacts mobile.
    MoreTileModel(
        title = "Contacts",
        subtitle = "Relations et timeline iMessage",
        kind = MoreTileKind.FuturePlaceholder,
        route = null,
        futureFlagId = "JARVIS-FUTURE-CONTACTS",
    ),
    // TODO(JARVIS-FUTURE-AUTOMATIONS): activer le centre d'automatisations.
    MoreTileModel(
        title = "Automatisations",
        subtitle = "Routines et actions programmées",
        kind = MoreTileKind.FuturePlaceholder,
        route = null,
        futureFlagId = "JARVIS-FUTURE-AUTOMATIONS",
    ),
)
