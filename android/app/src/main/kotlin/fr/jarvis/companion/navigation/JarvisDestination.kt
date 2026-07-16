package fr.jarvis.companion.navigation

object JarvisDestination {
    const val ONBOARDING = "onboarding"
    const val HOME = "home"
    const val CHAT = "chat"
    const val CHAT_DETAIL = "chat/{localId}"

    fun chatDetail(localId: Long) = "chat/$localId"
    const val VOICE = "voice"
    const val CALENDAR = "calendar"
    const val MORE = "more"
    const val TASKS = "tasks"
    const val LOCATION = "location"
    const val NOTIFICATIONS = "notifications"
    const val DIAGNOSTICS = "diagnostics"
    const val SETTINGS = "settings"
    const val REPAIR = "repair"

    val bottomBarRoutes = setOf(HOME, CHAT, CALENDAR, MORE)
}
