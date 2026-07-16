package fr.jarvis.companion.feature.repair

enum class RepairActionType {
    RevokeToken,
    RelaunchOnboarding,
}

data class RepairActionModel(
    val type: RepairActionType,
    val title: String,
    val description: String,
    val ctaLabel: String,
    val confirmTitle: String,
    val confirmMessage: String,
    val requiresConfirmation: Boolean,
    val dangerZone: Boolean,
)

fun buildRepairActions(): List<RepairActionModel> = listOf(
    RepairActionModel(
        type = RepairActionType.RevokeToken,
        title = "Jeton d'appairage",
        description = "Révoque uniquement le jeton stocké sur ce téléphone.",
        ctaLabel = "Révoquer le jeton local",
        confirmTitle = "Confirmer la révocation du jeton local ?",
        confirmMessage = "Les données locales restent intactes.",
        requiresConfirmation = true,
        dangerZone = true,
    ),
    RepairActionModel(
        type = RepairActionType.RelaunchOnboarding,
        title = "Onboarding",
        description = "Relance l'assistant de configuration sans effacer d'autres données.",
        ctaLabel = "Relancer l'onboarding",
        confirmTitle = "Confirmer la relance de l'onboarding ?",
        confirmMessage = "Le jeton local sera révoqué puis l'écran d'onboarding sera réouvert.",
        requiresConfirmation = true,
        dangerZone = true,
    ),
)
