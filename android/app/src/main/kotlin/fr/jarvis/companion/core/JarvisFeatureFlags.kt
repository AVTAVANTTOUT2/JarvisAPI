package fr.jarvis.companion.core

/**
 * Feature flags centralisés des fonctionnalités futures.
 *
 * Registre complet : android/docs/FUTURE_FEATURES.md.
 * Un flag à `false` affiche au plus un placeholder inerte (« Bientôt ») — jamais une
 * fonctionnalité simulée. Basculer un flag à `true` sans brancher la logique associée
 * ne doit rien casser : les écrans vérifient le flag ET la disponibilité réelle.
 */
object JarvisFeatureFlags {
    // TODO(JARVIS-FUTURE-VOICE-CONTINUOUS): brancher le service de conversation
    // vocale continue (VAD + anti-écho) quand le pipeline audio continu existera.
    const val CONTINUOUS_VOICE = false

    // TODO(JARVIS-FUTURE-WAKE-ADVANCED): options wake word avancées (sensibilité,
    // modèle custom) au-delà du toggle Porcupine actuel.
    const val WAKE_WORD_ADVANCED = false

    // TODO(JARVIS-FUTURE-LIVE-MAP): carte de localisation live —
    // backend GET /api/location/history à exposer au Bearer mobile.
    const val LIVE_MAP = false

    // TODO(JARVIS-FUTURE-TRIPS-HISTORY): historique détaillé des trajets —
    // backend GET /api/trips.
    const val TRIPS_HISTORY = false

    // TODO(JARVIS-FUTURE-CALENDAR-CREATE): création/édition d'événements —
    // backend POST /api/calendar/* en Bearer mobile.
    const val CALENDAR_CREATE = false

    // TODO(JARVIS-FUTURE-TASKS-MUTATIONS): création/complétion de tâches offline-first
    // (modèle : file pending_chat_operations) — mutations /api/tasks Bearer.
    const val TASKS_MUTATIONS = false

    // TODO(JARVIS-FUTURE-CHAT-ATTACHMENTS): pièces jointes dans le composer —
    // POST /api/conversations/{id}/upload version Bearer.
    const val CHAT_ATTACHMENTS = false

    // TODO(JARVIS-FUTURE-SLASH-COMMANDS): commandes slash locales alignées sur le
    // composer web (/nouveau /cherche /briefing /tâche).
    const val SLASH_COMMANDS = false

    // TODO(JARVIS-FUTURE-NOTIFICATIONS-CENTER): actions marquer-lu —
    // POST /api/notifications/{id}/read en Bearer mobile.
    const val NOTIFICATIONS_ACTIONS = false

    // TODO(JARVIS-FUTURE-MULTI-DEVICE): gestion multi-appareils — GET /api/devices.
    const val MULTI_DEVICE = false

    // TODO(JARVIS-FUTURE-OFFLINE-DETAIL): vue détaillée de la file hors ligne.
    const val OFFLINE_DETAIL = false

    // TODO(JARVIS-FUTURE-MEMORY-VIEW): vue mémoire JARVIS — /api/memory.
    const val MEMORY_VIEW = false

    // TODO(JARVIS-FUTURE-CONTACTS): contacts et relations — /api/people.
    const val CONTACTS_VIEW = false

    // TODO(JARVIS-FUTURE-AUTOMATIONS): automatisations et contrôle des services.
    const val AUTOMATIONS = false

    // TODO(JARVIS-FUTURE-WIDGETS): widgets configurables sur l'écran d'accueil Android.
    const val HOME_WIDGETS = false

    // TODO(JARVIS-FUTURE-DASHBOARD-CUSTOM): personnalisation du tableau de bord.
    const val DASHBOARD_CUSTOM = false
}
