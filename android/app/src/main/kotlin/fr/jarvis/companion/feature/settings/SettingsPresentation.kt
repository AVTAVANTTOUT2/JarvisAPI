package fr.jarvis.companion.feature.settings

import fr.jarvis.companion.network.ServerUrlNormalizer

data class ServerSaveEvaluation(
    val normalizedServerUrl: String?,
    val errorMessage: String?,
    val successMessage: String?,
    val shouldRevokeLocalToken: Boolean,
)

data class FutureSettingOption(
    val title: String,
    val description: String,
    val futureFlagId: String,
)

fun evaluateServerSave(
    rawInput: String,
    currentServer: String,
    normalizer: (String) -> String? = ServerUrlNormalizer::normalize,
): ServerSaveEvaluation {
    val normalized = normalizer(rawInput)
    if (normalized == null) {
        return ServerSaveEvaluation(
            normalizedServerUrl = null,
            errorMessage = "Adresse invalide",
            successMessage = null,
            shouldRevokeLocalToken = false,
        )
    }
    val changed = normalized != currentServer
    return ServerSaveEvaluation(
        normalizedServerUrl = normalized,
        errorMessage = null,
        successMessage = if (changed) {
            "Serveur enregistré. Jeton local révoqué."
        } else {
            "Serveur déjà enregistré."
        },
        shouldRevokeLocalToken = changed,
    )
}

fun sanitizePorcupineKey(rawValue: String): String? = rawValue.trim().ifBlank { null }

fun buildFutureSettingsOptions(): List<FutureSettingOption> = listOf(
    // TODO(JARVIS-FUTURE-MULTI-DEVICE): afficher la gestion réelle des appareils
    // lorsque l'API mobile /api/devices sera branchée côté Android.
    FutureSettingOption(
        title = "Multi-device",
        description = "Gestion de plusieurs téléphones et priorités d'appareil.",
        futureFlagId = "JARVIS-FUTURE-MULTI-DEVICE",
    ),
    // TODO(JARVIS-FUTURE-WAKE-ADVANCED): exposer sensibilité/modèle wake word.
    FutureSettingOption(
        title = "Wake word avancé",
        description = "Réglages avancés de sensibilité et profils vocaux.",
        futureFlagId = "JARVIS-FUTURE-WAKE-ADVANCED",
    ),
    // TODO(JARVIS-FUTURE-WIDGETS): widgets Android inertes tant que le backend
    // de personnalisation dashboard n'est pas disponible.
    FutureSettingOption(
        title = "Widgets d'accueil",
        description = "Widgets Android pour le briefing et les alertes urgentes.",
        futureFlagId = "JARVIS-FUTURE-WIDGETS",
    ),
)
