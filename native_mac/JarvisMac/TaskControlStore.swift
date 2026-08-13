import Foundation
import SwiftUI

/// État observable de la section Tâches.
///
/// Trois règles tenues ici :
///
/// * **Le backend est la seule source de vérité.** Aucune méthode ne fabrique
///   un statut : après une mutation, l'état affiché est celui que le serveur
///   a renvoyé. En cas d'erreur réseau, l'écran garde ce qu'il savait et le
///   dit, plutôt que d'inventer une progression.
/// * **La reprise se fait par rang, pas par horodatage.** L'activité est
///   rechargée avec `after_sequence` : une application réveillée après une
///   suspension récupère ce qui manque, sans doublon ni trou.
/// * **Le rafraîchissement s'arrête quand l'écran ne sert à rien.** Le
///   polling est suspendu dès que la section n'est plus visible.
@MainActor
final class TaskControlStore: ObservableObject {

    @Published private(set) var tasks: [ControlTask] = []
    @Published private(set) var candidates: [TaskCandidate] = []
    @Published private(set) var counts: [String: Int] = [:]
    @Published private(set) var isLoading = false
    @Published private(set) var isMutating = false

    @Published var section: TaskControlSection = .toApprove
    @Published var searchText: String = ""
    @Published var selectedTaskID: String?

    // Détail
    @Published private(set) var detail: TaskDetailEnvelope?
    @Published private(set) var activity: [TaskActivityEntry] = []
    @Published private(set) var approvals: [TaskEffectApproval] = []
    @Published private(set) var artifacts: [TaskArtifact] = []
    @Published private(set) var report: TaskReport?
    @Published private(set) var isDetailLoading = false

    /// Message d'erreur affiché tel quel. Une panne de transport ne doit pas
    /// se traduire par un état de tâche fabriqué côté client.
    @Published var statusMessage: String?
    @Published var errorMessage: String?

    private let api: JarvisAPI
    private let notifier: TaskNotificationCenter
    private var refreshTask: Task<Void, Never>?
    private var detailTask: Task<Void, Never>?
    private var lastActivitySequence = 0
    private var isVisible = false

    init(api: JarvisAPI, notifier: TaskNotificationCenter = .shared) {
        self.api = api
        self.notifier = notifier
    }

    // MARK: - Cycle de vie

    func appear() {
        guard !isVisible else { return }
        isVisible = true
        startPolling()
    }

    func disappear() {
        isVisible = false
        refreshTask?.cancel()
        refreshTask = nil
        detailTask?.cancel()
        detailTask = nil
    }

    private func startPolling() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(6))
            }
        }
    }

    // MARK: - Listes

    func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            if section.isCandidateSection {
                candidates = try await api.taskCandidates()
                // Les compteurs restent alimentés par la route des tâches :
                // la barre latérale doit rester juste même sur cet onglet.
                let envelope = try await api.controlTasks(section: nil)
                counts = envelope.counts
            } else {
                let envelope = try await api.controlTasks(section: section)
                tasks = envelope.tasks
                counts = envelope.counts
            }
            errorMessage = nil

            // Les tâches qui demandent une décision sont relues séparément :
            // réconcilier sur la seule section affichée aurait rendu les
            // alertes dépendantes de l'onglet ouvert — rester sur
            // « Terminées » aurait fait taire les plans à valider. La section
            // courante est ajoutée pour que les conclusions vues à l'écran
            // notifient aussi.
            let attention = try await api.controlTasks(section: .attention)
            var byID: [String: ControlTask] = [:]
            for task in attention.tasks + tasks { byID[task.id] = task }
            notifier.reconcile(tasks: Array(byID.values), candidates: candidates)

            if let selectedTaskID, tasks.contains(where: { $0.id == selectedTaskID }) {
                await loadDetail(taskID: selectedTaskID, force: false)
            }
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }

    var visibleTasks: [ControlTask] {
        let needle = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return tasks }
        return tasks.filter {
            $0.title.lowercased().contains(needle)
                || $0.description.lowercased().contains(needle)
                || $0.source.subject.lowercased().contains(needle)
        }
    }

    var attentionCount: Int { counts["attention"] ?? 0 }

    func count(for section: TaskControlSection) -> Int {
        section.isCandidateSection ? candidates.count : (counts[section.rawValue] ?? 0)
    }

    // MARK: - Détail

    func select(taskID: String?) {
        selectedTaskID = taskID
        detail = nil
        activity = []
        approvals = []
        artifacts = []
        report = nil
        lastActivitySequence = 0
        guard let taskID else { return }
        Task { await loadDetail(taskID: taskID, force: true) }
    }

    func loadDetail(taskID: String, force: Bool) async {
        if force { isDetailLoading = true }
        defer { isDetailLoading = false }
        do {
            let envelope = try await api.controlTaskDetail(id: taskID)
            guard selectedTaskID == taskID else { return }
            detail = envelope
            report = envelope.report

            let increment = try await api.controlTaskActivity(
                id: taskID,
                afterSequence: force ? 0 : lastActivitySequence
            )
            guard selectedTaskID == taskID else { return }
            if force {
                activity = increment.activity
            } else if !increment.activity.isEmpty {
                activity.append(contentsOf: increment.activity)
            }
            lastActivitySequence = max(lastActivitySequence, increment.lastSequence)

            if envelope.task.status.isExecuting {
                approvals = (try? await api.controlTaskApprovals(id: taskID)) ?? approvals
            }
            if envelope.task.status == .completed || envelope.task.resultStatus != nil {
                artifacts = (try? await api.controlTaskArtifacts(id: taskID)) ?? artifacts
                if report == nil {
                    report = try? await api.controlTaskReport(id: taskID)
                }
            }
            errorMessage = nil
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }

    var selectedTask: ControlTask? {
        guard let selectedTaskID else { return nil }
        return detail?.task ?? tasks.first { $0.id == selectedTaskID }
    }

    var currentPlan: TaskPlan? { detail?.currentPlan ?? detail?.plans.first }

    var pendingApprovals: [TaskEffectApproval] { approvals.filter(\.isPending) }

    // MARK: - Mutations

    func createTask(
        title: String,
        description: String,
        priority: String,
        dueAt: Date?,
        projectID: String?,
        comment: String
    ) async {
        await mutate("Tâche créée — le plan attend votre validation.") {
            let task = try await self.api.createControlTask(
                title: title,
                description: description,
                priority: priority,
                dueAt: dueAt,
                projectID: projectID,
                comment: comment
            )
            self.section = .toApprove
            await self.refresh()
            self.select(taskID: task.id)
        }
    }

    /// Accepte le plan **affiché**. Le digest voyage avec la décision : si le
    /// plan a changé entre-temps, le serveur refuse et l'écran le dit, plutôt
    /// que de lancer un travail que personne n'a lu.
    func decidePlan(_ decision: String, comment: String = "") async {
        guard let task = selectedTask, let plan = currentPlan else { return }
        let confirmation: String
        switch decision {
        case "approved": confirmation = "Plan accepté — exécution lancée."
        case "rejected": confirmation = "Plan refusé — rien n'a été exécuté."
        default: confirmation = "Révision demandée — un nouveau plan est en préparation."
        }
        await mutate(confirmation) {
            _ = try await self.api.decideControlPlan(
                taskID: task.id,
                version: plan.version,
                decision: decision,
                comment: comment,
                digest: plan.digest.isEmpty ? nil : plan.digest
            )
            await self.refresh()
            await self.loadDetail(taskID: task.id, force: true)
        }
    }

    func decideApproval(_ approval: TaskEffectApproval, approved: Bool) async {
        guard let task = selectedTask else { return }
        await mutate(approved ? "Autorisation accordée." : "Autorisation refusée.") {
            try await self.api.decideEffectApproval(
                taskID: task.id,
                approvalID: approval.approvalID,
                approved: approved
            )
            await self.loadDetail(taskID: task.id, force: true)
        }
    }

    func cancelTask(reason: String = "") async {
        guard let task = selectedTask else { return }
        await mutate("Tâche annulée.") {
            _ = try await self.api.cancelControlTask(taskID: task.id, reason: reason)
            await self.refresh()
            await self.loadDetail(taskID: task.id, force: true)
        }
    }

    func addComment(_ body: String, requestPlanRevision: Bool) async {
        guard let task = selectedTask else { return }
        await mutate(
            requestPlanRevision
                ? "Révision demandée — un nouveau plan devra être validé."
                : "Commentaire enregistré."
        ) {
            _ = try await self.api.addControlComment(
                taskID: task.id,
                body: body,
                requestPlanRevision: requestPlanRevision
            )
            await self.refresh()
            await self.loadDetail(taskID: task.id, force: true)
        }
    }

    func decideCandidate(_ candidate: TaskCandidate, decision: String) async {
        await mutate(
            decision == "accepted"
                ? "Tâche créée — son plan attend votre validation."
                : "Suggestion écartée."
        ) {
            let envelope = try await self.api.decideTaskCandidate(
                candidateID: candidate.candidateID,
                decision: decision,
                mergeInto: nil
            )
            if let task = envelope.task {
                self.section = .toApprove
                await self.refresh()
                self.select(taskID: task.id)
            } else {
                await self.refresh()
            }
        }
    }

    private func mutate(_ confirmation: String, _ work: @escaping () async throws -> Void) async {
        guard !isMutating else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            try await work()
            statusMessage = confirmation
            errorMessage = nil
        } catch {
            statusMessage = nil
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        }
    }
}
