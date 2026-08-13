import Foundation

/// Accès HTTP au pilotage de tâches.
///
/// L'application est un client : elle n'appelle jamais un runtime d'exécution,
/// ne connaît aucun identifiant de fournisseur, ne détient aucune clé de
/// modèle et ne décide jamais qu'un run est terminé. Toutes les méthodes de ce
/// fichier tapent exclusivement sur les contrats génériques `/api/task-control`
/// et `/api/task-candidates`.
extension JarvisAPI {

    // MARK: - Lecture

    func controlTasks(section: TaskControlSection?, limit: Int = 100) async throws -> TaskListEnvelope {
        var suffix = "?limit=\(limit)"
        if let section, !section.isCandidateSection {
            suffix += "&section=\(section.rawValue)"
        }
        return try await request("/api/task-control/tasks\(suffix)")
    }

    func controlTaskDetail(id: String) async throws -> TaskDetailEnvelope {
        try await request("/api/task-control/tasks/\(pathSegment(id))")
    }

    func controlTaskPlans(id: String) async throws -> [TaskPlan] {
        let envelope: TaskPlansEnvelope = try await request(
            "/api/task-control/tasks/\(pathSegment(id))/plans"
        )
        return envelope.plans
    }

    /// Activité depuis un rang connu. `afterSequence` est ce qui rend la
    /// reprise après veille exempte de doublons : le serveur ne renvoie que
    /// ce qui manque, et le rang reste monotone par tâche.
    func controlTaskActivity(
        id: String,
        afterSequence: Int = 0,
        level: String? = nil
    ) async throws -> TaskActivityEnvelope {
        let path = "/api/task-control/tasks/\(pathSegment(id))/activity"
        var query = "after_sequence=\(max(0, afterSequence))&limit=500"
        if let level { query += "&level=\(level)" }
        return try await request("\(path)?\(query)")
    }

    func controlTaskApprovals(id: String) async throws -> [TaskEffectApproval] {
        let envelope: TaskApprovalsEnvelope = try await request(
            "/api/task-control/tasks/\(pathSegment(id))/approvals"
        )
        return envelope.approvals
    }

    /// Retourne `nil` quand aucun rapport n'existe encore : une tâche en cours
    /// n'a pas de conclusion, et un 404 n'est pas une panne.
    func controlTaskReport(id: String) async throws -> TaskReport? {
        do {
            let envelope: TaskReportEnvelope = try await request(
                "/api/task-control/tasks/\(pathSegment(id))/report"
            )
            return envelope.report
        } catch JarvisAPIError.http(404, _) {
            return nil
        }
    }

    func controlTaskArtifacts(id: String) async throws -> [TaskArtifact] {
        let envelope: TaskArtifactsEnvelope = try await request(
            "/api/task-control/tasks/\(pathSegment(id))/artifacts"
        )
        return envelope.artifacts
    }

    func taskCandidates() async throws -> [TaskCandidate] {
        let envelope: TaskCandidatesEnvelope = try await request("/api/task-candidates")
        return envelope.candidates
    }

    // MARK: - Mutations

    func createControlTask(
        title: String,
        description: String,
        priority: String,
        dueAt: Date?,
        projectID: String?,
        comment: String
    ) async throws -> ControlTask {
        struct Body: Encodable {
            let title: String
            let description: String
            let priority: String
            let source_type = "manual"
            let source_channel = "macos"
            let due_at: String?
            let project_id: String?
            let comment: String
        }
        let envelope: ControlTaskEnvelope = try await request(
            "/api/task-control/tasks",
            method: "POST",
            body: Body(
                title: title,
                description: description,
                priority: priority,
                due_at: dueAt.map { ISO8601DateFormatter().string(from: $0) },
                project_id: projectID?.isEmpty == false ? projectID : nil,
                comment: comment
            )
        )
        return envelope.task
    }

    /// Tranche une version de plan.
    ///
    /// `digest` est celui qui était affiché à l'écran. Le serveur refuse en 409
    /// s'il ne correspond plus : c'est ce qui empêche d'approuver un plan que
    /// l'utilisateur n'a pas lu, quand une révision est arrivée entre-temps.
    func decideControlPlan(
        taskID: String,
        version: Int,
        decision: String,
        comment: String,
        digest: String?
    ) async throws -> ControlTask {
        struct Body: Encodable {
            let decision: String
            let comment: String
            let plan_digest: String?
        }
        let envelope: ControlTaskEnvelope = try await request(
            "/api/task-control/tasks/\(pathSegment(taskID))/plans/\(version)/decision",
            method: "POST",
            body: Body(decision: decision, comment: comment, plan_digest: digest)
        )
        return envelope.task
    }

    func replanControlTask(taskID: String, comment: String) async throws -> ControlTask {
        struct Body: Encodable { let comment: String }
        struct Envelope: Decodable { let task: ControlTask }
        let envelope: Envelope = try await request(
            "/api/task-control/tasks/\(pathSegment(taskID))/plan",
            method: "POST",
            body: Body(comment: comment)
        )
        return envelope.task
    }

    func cancelControlTask(taskID: String, reason: String) async throws -> ControlTask {
        struct Body: Encodable { let reason: String }
        let envelope: ControlTaskEnvelope = try await request(
            "/api/task-control/tasks/\(pathSegment(taskID))/cancel",
            method: "POST",
            body: Body(reason: reason)
        )
        return envelope.task
    }

    func addControlComment(
        taskID: String,
        body: String,
        requestPlanRevision: Bool
    ) async throws -> TaskCommentEnvelope {
        struct Body: Encodable {
            let body: String
            let request_plan_revision: Bool
        }
        return try await request(
            "/api/task-control/tasks/\(pathSegment(taskID))/comments",
            method: "POST",
            body: Body(body: body, request_plan_revision: requestPlanRevision)
        )
    }

    /// Autorise ou refuse **un** effet précis. Jamais une catégorie d'effets,
    /// jamais pour la suite du run.
    func decideEffectApproval(
        taskID: String,
        approvalID: String,
        approved: Bool
    ) async throws {
        struct Body: Encodable { let decision: String }
        struct Empty: Decodable {
            init(from decoder: Decoder) throws { _ = decoder }
        }
        let _: Empty = try await request(
            "/api/task-control/tasks/\(pathSegment(taskID))/approvals/\(pathSegment(approvalID))/decision",
            method: "POST",
            body: Body(decision: approved ? "approved" : "denied")
        )
    }

    func decideTaskCandidate(
        candidateID: String,
        decision: String,
        mergeInto: String?
    ) async throws -> TaskCandidateDecisionEnvelope {
        struct Body: Encodable {
            let decision: String
            let merge_into: String?
        }
        return try await request(
            "/api/task-candidates/\(pathSegment(candidateID))/decision",
            method: "POST",
            body: Body(decision: decision, merge_into: mergeInto)
        )
    }
}
