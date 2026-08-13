import Foundation
import UserNotifications

/// Notifications macOS du pilotage de tâches.
///
/// Quatre décisions structurantes :
///
/// * **Rien de sensible sur l'écran verrouillé.** Le corps de la notification
///   ne contient jamais le contenu de la demande, l'extrait de l'e-mail, ni
///   les arguments d'une action. Il dit ce qui est attendu et le titre de la
///   tâche, rien de plus.
/// * **Une notification n'approuve rien.** Aucun bouton d'action n'accorde
///   d'autorisation : l'unique action ouvre la tâche dans l'application, là où
///   l'utilisateur voit l'effet exact avant de trancher.
/// * **Déduplication par état.** Une même tâche dans le même état ne notifie
///   qu'une fois ; repasser en attente après une reprise notifie de nouveau.
/// * **Retrait quand c'est traité.** Une tâche qui n'attend plus rien voit sa
///   notification livrée retirée et le badge recalculé.
@MainActor
final class TaskNotificationCenter {

    static let shared = TaskNotificationCenter()

    /// Rattachement de la notification cliquée, lu par la coque applicative.
    var onOpenTask: ((String) -> Void)?

    private let center = UNUserNotificationCenter.current()
    private var authorized = false
    private var delivered: [String: String] = [:]
    private var knownCandidates: Set<String> = []
    private let delegate = TaskNotificationDelegate()

    private init() {
        delegate.owner = self
        center.delegate = delegate
    }

    func requestAuthorization() {
        center.requestAuthorization(options: [.alert, .sound, .badge]) { [weak self] granted, _ in
            Task { @MainActor in self?.authorized = granted }
        }
    }

    /// Aligne les notifications sur l'état réel. Appelée à chaque relevé :
    /// c'est le serveur qui décide de ce qui mérite l'attention, pas un
    /// compteur local qui dériverait.
    func reconcile(tasks: [ControlTask], candidates: [TaskCandidate]) {
        guard authorized else { return }
        var stillPending: Set<String> = []

        for task in tasks where task.status.needsAttention || task.status == .failed
            || task.status == .completed
        {
            let key = task.id
            let signature = task.status.rawValue
            if task.status.needsAttention { stillPending.insert(key) }
            guard delivered[key] != signature else { continue }
            delivered[key] = signature
            deliver(for: task)
        }

        for candidate in candidates where candidate.decision == "pending" {
            guard !knownCandidates.contains(candidate.candidateID) else { continue }
            knownCandidates.insert(candidate.candidateID)
            deliverCandidate(candidate)
        }

        // Une tâche qui n'attend plus rien ne doit pas laisser sa bannière.
        let obsolete = delivered.keys.filter { !stillPending.contains($0) }
        if !obsolete.isEmpty {
            center.removeDeliveredNotifications(withIdentifiers: obsolete.map { "jarvis.task.\($0)" })
        }
        updateBadge(count: stillPending.count)
    }

    private func deliver(for task: ControlTask) {
        let content = UNMutableNotificationContent()
        content.threadIdentifier = "jarvis.task.\(task.id)"
        content.userInfo = ["task_id": task.id]
        content.sound = task.status == .awaitingPermission ? .default : nil

        switch task.status {
        case .awaitingPlanApproval:
            content.title = "Plan prêt à être vérifié"
            content.body = "« \(task.title) » attend votre validation avant tout démarrage."
        case .awaitingPermission:
            content.title = "JARVIS requiert votre attention"
            content.body = "Une autorisation est nécessaire pour continuer la tâche « \(task.title) »."
        case .blocked:
            content.title = "Tâche bloquée"
            content.body = "« \(task.title) » ne peut pas continuer sans intervention."
        case .failed:
            content.title = "Tâche en échec"
            content.body = "« \(task.title) » s'est arrêtée sans aboutir."
        case .completed:
            content.title = "Tâche terminée"
            content.body = "Le résultat de « \(task.title) » est disponible."
        default:
            return
        }

        submit(identifier: "jarvis.task.\(task.id)", content: content)
    }

    private func deliverCandidate(_ candidate: TaskCandidate) {
        let content = UNMutableNotificationContent()
        content.title = "Demande détectée"
        // Le titre suggéré vient d'un contenu observé : il est déjà borné et
        // redacté côté serveur, et rien d'autre de la source n'est repris.
        content.body = "« \(candidate.suggestedTitle) » — à confirmer avant toute action."
        content.threadIdentifier = "jarvis.candidate"
        submit(identifier: "jarvis.candidate.\(candidate.candidateID)", content: content)
    }

    private func submit(identifier: String, content: UNMutableNotificationContent) {
        center.add(
            UNNotificationRequest(identifier: identifier, content: content, trigger: nil)
        )
    }

    private func updateBadge(count: Int) {
        let content = UNMutableNotificationContent()
        content.badge = NSNumber(value: count)
        // Le badge est porté par le centre de notifications plutôt que par
        // NSApp : il reste juste même quand la fenêtre est fermée.
        center.setBadgeCount(count) { _ in }
        _ = content
    }

    fileprivate func handleOpen(taskID: String) {
        onOpenTask?(taskID)
    }
}

/// Délégué séparé : `UNUserNotificationCenterDelegate` doit être une classe
/// Objective-C, et le centre n'en retient qu'une référence faible.
private final class TaskNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    weak var owner: TaskNotificationCenter?

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let info = response.notification.request.content.userInfo
        if let taskID = info["task_id"] as? String {
            Task { @MainActor [weak self] in self?.owner?.handleOpen(taskID: taskID) }
        }
        completionHandler()
    }
}
