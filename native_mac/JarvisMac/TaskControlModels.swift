import Foundation
import SwiftUI

// MARK: - États

/// Miroir client de la machine à états du serveur.
///
/// Le client ne décide jamais d'un état : il le lit. `unknown` existe pour
/// qu'une version serveur plus récente n'écrase pas l'écran d'une erreur de
/// décodage — un état inconnu s'affiche tel quel, sans prétendre le
/// comprendre.
enum TaskControlStatus: String, Codable, CaseIterable {
    case candidate
    case created
    case planning
    case awaitingPlanApproval = "awaiting_plan_approval"
    case planRejected = "plan_rejected"
    case planRevisionRequested = "plan_revision_requested"
    case approved
    case queued
    case resourceWait = "resource_wait"
    case running
    case awaitingPermission = "awaiting_permission"
    case cancelling
    case verifying
    case completed
    case blocked
    case failed
    case cancelled
    case archived
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TaskControlStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .candidate: "Détectée"
        case .created: "Créée"
        case .planning: "Planification"
        case .awaitingPlanApproval: "Plan à valider"
        case .planRejected: "Plan refusé"
        case .planRevisionRequested: "Révision demandée"
        case .approved: "Approuvée"
        case .queued: "En file"
        case .resourceWait: "Attente de ressources"
        case .running: "En cours"
        case .awaitingPermission: "Autorisation requise"
        case .cancelling: "Annulation"
        case .verifying: "Vérification"
        case .completed: "Terminée"
        case .blocked: "Bloquée"
        case .failed: "Échec"
        case .cancelled: "Annulée"
        case .archived: "Archivée"
        case .unknown: "État inconnu"
        }
    }

    var tint: Color {
        switch self {
        case .awaitingPlanApproval, .awaitingPermission: .orange
        case .running, .verifying, .queued, .resourceWait, .approved: .blue
        case .completed: .green
        case .failed, .blocked: .red
        case .cancelled, .archived, .planRejected: .secondary
        default: .secondary
        }
    }

    var symbol: String {
        switch self {
        case .awaitingPlanApproval: "doc.text.magnifyingglass"
        case .awaitingPermission: "hand.raised.fill"
        case .running, .queued, .resourceWait, .approved: "gearshape.2.fill"
        case .verifying: "checkmark.shield"
        case .completed: "checkmark.seal.fill"
        case .failed: "xmark.octagon.fill"
        case .blocked: "exclamationmark.triangle.fill"
        case .cancelled, .archived: "archivebox"
        case .planning: "wand.and.stars"
        default: "circle"
        }
    }

    /// Vrai quand la tâche attend un geste humain.
    var needsAttention: Bool {
        self == .awaitingPlanApproval || self == .awaitingPermission || self == .blocked
    }

    /// Vrai quand un runtime peut légitimement tourner.
    var isExecuting: Bool {
        switch self {
        case .queued, .resourceWait, .running, .awaitingPermission, .verifying, .cancelling:
            true
        default:
            false
        }
    }
}

enum TaskControlSection: String, CaseIterable, Identifiable {
    case toApprove = "to_approve"
    case attention
    case planned
    case running
    case completed
    case failed
    case archived
    case candidates

    var id: String { rawValue }

    var title: String {
        switch self {
        case .toApprove: "À valider"
        case .attention: "Attention requise"
        case .planned: "Planifiées"
        case .running: "En cours"
        case .completed: "Terminées"
        case .failed: "Bloquées / Échecs"
        case .archived: "Archives"
        case .candidates: "Détectées"
        }
    }

    var subtitle: String {
        switch self {
        case .toApprove: "Plans prêts pour votre décision"
        case .attention: "Autorisations ou blocages à traiter"
        case .planned: "Missions acceptées avant démarrage"
        case .running: "Travail actuellement exécuté par Jarvis"
        case .completed: "Résultats prêts à consulter"
        case .failed: "Missions interrompues ou bloquées"
        case .archived: "Historique rangé"
        case .candidates: "Demandes repérées à confirmer"
        }
    }

    var symbol: String {
        switch self {
        case .toApprove: "checkmark.rectangle.stack"
        case .attention: "bell.badge.fill"
        case .planned: "calendar"
        case .running: "bolt.horizontal.circle"
        case .completed: "checkmark.circle"
        case .failed: "exclamationmark.triangle"
        case .archived: "archivebox"
        case .candidates: "sparkle.magnifyingglass"
        }
    }

    /// Les candidats ne sont pas des tâches : ils viennent d'une autre route.
    var isCandidateSection: Bool { self == .candidates }
}

// MARK: - Provenance

enum TaskControlSourceType: String, Codable {
    case manual, userRequest = "user_request", message, email, scheduler, unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TaskControlSourceType(rawValue: raw) ?? .unknown
    }

    var symbol: String {
        switch self {
        case .manual: "hand.point.up.left"
        case .userRequest: "person.wave.2"
        case .message: "message"
        case .email: "envelope"
        case .scheduler: "clock"
        case .unknown: "questionmark.circle"
        }
    }

    var label: String {
        switch self {
        case .manual: "Créée à la main"
        case .userRequest: "Demandée à JARVIS"
        case .message: "Détectée dans un message"
        case .email: "Détectée dans un e-mail"
        case .scheduler: "Planificateur"
        case .unknown: "Provenance inconnue"
        }
    }
}

struct TaskControlSource: Codable, Hashable {
    var sourceType: TaskControlSourceType = .manual
    var channel: String = "api"
    var reference: String = ""
    var excerpt: String = ""
    var confidence: Double?
    var detectionReason: String = ""
    var sender: String = ""
    var subject: String = ""

    enum CodingKeys: String, CodingKey {
        case channel, reference, excerpt, confidence, sender, subject
        case sourceType = "source_type"
        case detectionReason = "detection_reason"
    }

    init() {}

    /// Décodage tolérant aux champs absents.
    ///
    /// Le `Decodable` synthétisé exige toutes les clés non optionnelles, même
    /// quand la propriété a une valeur par défaut. Une provenance qui
    /// n'embarque pas d'extrait aurait alors fait échouer le décodage de toute
    /// la tâche — un écran vide pour un champ vide.
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sourceType =
            (try? values.decodeIfPresent(TaskControlSourceType.self, forKey: .sourceType))
            .flatMap { $0 } ?? .unknown
        channel = (try? values.decodeIfPresent(String.self, forKey: .channel)) as? String ?? "api"
        reference = (try? values.decodeIfPresent(String.self, forKey: .reference)) as? String ?? ""
        excerpt = (try? values.decodeIfPresent(String.self, forKey: .excerpt)) as? String ?? ""
        confidence = try? values.decodeIfPresent(Double.self, forKey: .confidence)
        detectionReason =
            (try? values.decodeIfPresent(String.self, forKey: .detectionReason)) as? String ?? ""
        sender = (try? values.decodeIfPresent(String.self, forKey: .sender)) as? String ?? ""
        subject = (try? values.decodeIfPresent(String.self, forKey: .subject)) as? String ?? ""
    }
}

// MARK: - Tâche

struct ControlTask: Identifiable, Codable, Hashable {
    let taskID: String
    var title: String
    var description: String
    var status: TaskControlStatus
    var priority: String
    var source: TaskControlSource
    var projectID: String?
    var conversationID: String?
    var dueAt: String?
    var createdAt: String?
    var updatedAt: String?
    var planVersion: Int?
    var approvedPlanVersion: Int?
    var approvedPlanDigest: String?
    var agenticRunID: String?
    var currentPhase: String
    var progress: Double
    var attentionRequired: Bool
    var resultStatus: String?
    var finalReportID: String?

    var id: String { taskID }

    enum CodingKeys: String, CodingKey {
        case title, description, status, priority, source, progress
        case taskID = "task_id"
        case projectID = "project_id"
        case conversationID = "conversation_id"
        case dueAt = "due_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case planVersion = "plan_version"
        case approvedPlanVersion = "approved_plan_version"
        case approvedPlanDigest = "approved_plan_digest"
        case agenticRunID = "agentic_run_id"
        case currentPhase = "current_phase"
        case attentionRequired = "attention_required"
        case resultStatus = "result_status"
        case finalReportID = "final_report_id"
    }

    /// Mêmes raisons que pour la provenance : seuls l'identifiant, le titre et
    /// l'état sont indispensables. Le reste retombe sur un défaut lisible
    /// plutôt que de faire disparaître la tâche de la liste.
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        taskID = try values.decode(String.self, forKey: .taskID)
        title = try values.decode(String.self, forKey: .title)
        status = try values.decode(TaskControlStatus.self, forKey: .status)
        description = (try? values.decodeIfPresent(String.self, forKey: .description)) as? String ?? ""
        priority = (try? values.decodeIfPresent(String.self, forKey: .priority)) as? String ?? "medium"
        source =
            (try? values.decodeIfPresent(TaskControlSource.self, forKey: .source)).flatMap { $0 }
            ?? TaskControlSource()
        projectID = try? values.decodeIfPresent(String.self, forKey: .projectID)
        conversationID = try? values.decodeIfPresent(String.self, forKey: .conversationID)
        dueAt = try? values.decodeIfPresent(String.self, forKey: .dueAt)
        createdAt = try? values.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try? values.decodeIfPresent(String.self, forKey: .updatedAt)
        planVersion = try? values.decodeIfPresent(Int.self, forKey: .planVersion)
        approvedPlanVersion = try? values.decodeIfPresent(Int.self, forKey: .approvedPlanVersion)
        approvedPlanDigest = try? values.decodeIfPresent(String.self, forKey: .approvedPlanDigest)
        agenticRunID = try? values.decodeIfPresent(String.self, forKey: .agenticRunID)
        currentPhase = (try? values.decodeIfPresent(String.self, forKey: .currentPhase)) as? String ?? ""
        progress = (try? values.decodeIfPresent(Double.self, forKey: .progress)) as? Double ?? 0
        attentionRequired =
            (try? values.decodeIfPresent(Bool.self, forKey: .attentionRequired)) as? Bool ?? false
        resultStatus = try? values.decodeIfPresent(String.self, forKey: .resultStatus)
        finalReportID = try? values.decodeIfPresent(String.self, forKey: .finalReportID)
    }

    init(
        taskID: String,
        title: String,
        description: String = "",
        status: TaskControlStatus,
        priority: String = "medium",
        source: TaskControlSource = TaskControlSource(),
        projectID: String? = nil,
        conversationID: String? = nil,
        dueAt: String? = nil,
        createdAt: String? = nil,
        updatedAt: String? = nil,
        planVersion: Int? = nil,
        approvedPlanVersion: Int? = nil,
        approvedPlanDigest: String? = nil,
        agenticRunID: String? = nil,
        currentPhase: String = "",
        progress: Double = 0,
        attentionRequired: Bool = false,
        resultStatus: String? = nil,
        finalReportID: String? = nil
    ) {
        self.taskID = taskID
        self.title = title
        self.description = description
        self.status = status
        self.priority = priority
        self.source = source
        self.projectID = projectID
        self.conversationID = conversationID
        self.dueAt = dueAt
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.planVersion = planVersion
        self.approvedPlanVersion = approvedPlanVersion
        self.approvedPlanDigest = approvedPlanDigest
        self.agenticRunID = agenticRunID
        self.currentPhase = currentPhase
        self.progress = progress
        self.attentionRequired = attentionRequired
        self.resultStatus = resultStatus
        self.finalReportID = finalReportID
    }

    var priorityLabel: String {
        switch priority {
        case "high": "Prioritaire"
        case "low": "Secondaire"
        default: "Normal"
        }
    }

    var isDecidable: Bool { status == .awaitingPlanApproval }
    var isCancellable: Bool {
        status != .cancelled && status != .archived && status != .completed
    }
}

// MARK: - Plan

struct PlanStep: Codable, Hashable, Identifiable {
    let index: Int
    let title: String
    var detail: String = ""
    var expectedResult: String = ""
    var tools: [String] = []
    var permissions: [String] = []

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, title, detail, tools, permissions
        case expectedResult = "expected_result"
    }
}

struct TaskPlan: Codable, Hashable, Identifiable {
    let planID: String
    let taskID: String
    let version: Int
    var objective: String
    var summary: String
    var contextUnderstood: String = ""
    var steps: [PlanStep] = []
    var expectedDeliverables: [String] = []
    var toolsExpected: [String] = []
    var permissionsExpected: [String] = []
    /// Liste canonique remise au runtime au démarrage. C'est elle que la
    /// décision humaine engage : approuver le plan, c'est approuver ces
    /// capacités et aucune autre.
    ///
    /// Optionnelle à dessein : un serveur antérieur à ce contrat n'envoie pas
    /// la clé, et la synthèse `Decodable` de Swift échouerait sur tout le plan
    /// au lieu de l'afficher sans la liste. L'absence se lit comme une liste
    /// vide — ce que le serveur refuse d'exécuter, donc aucun droit implicite.
    var executionPermissions: [String]?
    var risks: [String] = []
    var assumptions: [String] = []
    var successCriteria: [String] = []
    var knownLimits: [String] = []
    var estimatedDurationS: Int?
    var estimatedCost: Double?
    var decision: String = "pending"
    var decisionComment: String = ""
    var createdAt: String?
    var digest: String = ""

    var id: String { planID }

    enum CodingKeys: String, CodingKey {
        case objective, summary, steps, risks, assumptions, decision, version, digest
        case planID = "plan_id"
        case taskID = "task_id"
        case contextUnderstood = "context_understood"
        case expectedDeliverables = "expected_deliverables"
        case toolsExpected = "tools_expected"
        case permissionsExpected = "permissions_expected"
        case executionPermissions = "execution_permissions"
        case successCriteria = "success_criteria"
        case knownLimits = "known_limits"
        case estimatedDurationS = "estimated_duration_s"
        case estimatedCost = "estimated_cost"
        case decisionComment = "decision_comment"
        case createdAt = "created_at"
    }

    /// Permissions dont l'effet sort de la machine — signalées avant la
    /// validation, parce qu'elles annoncent une future demande d'autorisation.
    var externalEffectPermissions: [String] {
        let external: Set<String> = ["mail:send", "message:send", "calendar:write", "git:push"]
        return permissionsExpected.filter { external.contains($0) }
    }

    var estimatedDurationLabel: String {
        guard let seconds = estimatedDurationS, seconds > 0 else { return "non estimée" }
        let minutes = seconds / 60
        return minutes >= 60 ? "environ \(minutes / 60) h \(minutes % 60) min" : "environ \(minutes) min"
    }
}

// MARK: - Activité

struct TaskActivityEntry: Codable, Hashable, Identifiable {
    let activityID: String
    let taskID: String
    var runID: String?
    let sequence: Int
    let eventType: String
    var summary: String
    var agentID: String = ""
    var agentRole: String = ""
    var phase: String = ""
    var toolName: String = ""
    var artifactReference: String = ""
    var status: String = ""
    var level: String = "detail"
    var createdAt: String?

    var id: String { activityID }

    enum CodingKeys: String, CodingKey {
        case sequence, summary, phase, status, level
        case activityID = "activity_id"
        case taskID = "task_id"
        case runID = "run_id"
        case eventType = "event_type"
        case agentID = "agent_id"
        case agentRole = "agent_role"
        case toolName = "tool_name"
        case artifactReference = "artifact_reference"
        case createdAt = "created_at"
    }

    var roleLabel: String {
        switch agentRole {
        case "executor": "Exécution"
        case "reviewer": "Revue"
        case "planner": "Planification"
        case "user": "Vous"
        default: agentRole.isEmpty ? "JARVIS" : agentRole
        }
    }

    var symbol: String {
        switch eventType {
        case "tool_started", "tool_completed": "wrench.and.screwdriver"
        case "file_read": "doc.text.magnifyingglass"
        case "file_changed": "doc.badge.gearshape"
        case "test_started", "test_result": "testtube.2"
        case "review_started", "review_result": "checkmark.shield"
        case "permission_requested": "hand.raised"
        case "permission_decided": "hand.thumbsup"
        case "error", "blocked": "exclamationmark.octagon"
        case "warning": "exclamationmark.triangle"
        case "user_comment": "text.bubble"
        case "completed": "checkmark.circle"
        default: "circle.fill"
        }
    }

    var isProblem: Bool {
        eventType == "error" || eventType == "blocked" || eventType == "warning"
    }
}

// MARK: - Autorisations d'effet

struct TaskEffectApproval: Codable, Hashable, Identifiable {
    let approvalID: String
    var kind: String = "effect_approval"
    var action: String
    var tool: String
    var summary: String
    var sanitizedArguments: [String: String] = [:]
    var risks: [String] = []
    var scope: String = "run"
    var expiresAt: String?
    var decision: String = "pending"

    var id: String { approvalID }

    enum CodingKeys: String, CodingKey {
        case action, tool, summary, risks, scope, decision, kind
        case approvalID = "approval_id"
        case sanitizedArguments = "sanitized_arguments"
        case expiresAt = "expires_at"
    }

    var isPending: Bool { decision == "pending" }
}

// MARK: - Rapport et livrables

struct TaskReport: Decodable, Hashable, Identifiable {
    let reportID: String
    let taskID: String
    let version: Int
    var resultStatus: String
    var summary: String
    var markdown: String
    var createdAt: String?
    var deliveries: [TaskDelivery] = []

    var id: String { reportID }

    enum CodingKeys: String, CodingKey {
        case version, summary, markdown, data
        case reportID = "report_id"
        case taskID = "task_id"
        case resultStatus = "result_status"
        case createdAt = "created_at"
    }

    private enum DataKeys: String, CodingKey { case deliveries }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        reportID = try values.decode(String.self, forKey: .reportID)
        taskID = try values.decode(String.self, forKey: .taskID)
        version = try values.decode(Int.self, forKey: .version)
        resultStatus = try values.decode(String.self, forKey: .resultStatus)
        summary = (try? values.decode(String.self, forKey: .summary)) ?? ""
        markdown = (try? values.decode(String.self, forKey: .markdown)) ?? ""
        createdAt = try? values.decodeIfPresent(String.self, forKey: .createdAt)
        if let data = try? values.nestedContainer(keyedBy: DataKeys.self, forKey: .data) {
            deliveries = (try? data.decode([TaskDelivery].self, forKey: .deliveries)) ?? []
        }
    }
}

struct TaskDelivery: Codable, Hashable, Identifiable {
    let type: String
    let reference: String
    var sha256: String = ""

    var id: String { "\(type):\(reference)" }

    var label: String {
        switch type {
        case "file": "Fichier"
        case "directory": "Dossier"
        case "commit": "Commit"
        case "branch": "Branche"
        case "pull_request": "Pull request"
        case "report": "Rapport"
        case "draft": "Brouillon"
        default: type
        }
    }

    /// Un livrable est ouvrable soit comme URL http(s), soit comme chemin local.
    var openableURL: URL? {
        if reference.hasPrefix("http://") || reference.hasPrefix("https://") {
            return URL(string: reference)
        }
        return nil
    }

    var localPath: String? {
        guard openableURL == nil, !reference.isEmpty else { return nil }
        return reference
    }
}

struct TaskArtifact: Codable, Hashable, Identifiable {
    let artifactID: String
    let type: String
    let reference: String
    var sha256: String?
    var sizeBytes: Int?

    var id: String { artifactID }

    enum CodingKeys: String, CodingKey {
        case type, reference, sha256
        case artifactID = "artifact_id"
        case sizeBytes = "size_bytes"
    }
}

// MARK: - Candidats

struct TaskCandidate: Codable, Hashable, Identifiable {
    let candidateID: String
    var suggestedTitle: String
    var suggestedDescription: String = ""
    var source: TaskControlSource = TaskControlSource()
    var confidence: Double = 0
    var reason: String = ""
    var decision: String = "pending"
    var duplicateOf: String?
    var createdTaskID: String?
    var createdAt: String?

    var id: String { candidateID }

    enum CodingKeys: String, CodingKey {
        case source, confidence, reason, decision
        case candidateID = "candidate_id"
        case suggestedTitle = "suggested_title"
        case suggestedDescription = "suggested_description"
        case duplicateOf = "duplicate_of"
        case createdTaskID = "created_task_id"
        case createdAt = "created_at"
    }

    var confidenceLabel: String { "\(Int((confidence * 100).rounded())) %" }
}

// MARK: - Commentaires et enveloppes

struct TaskComment: Codable, Hashable, Identifiable {
    let commentID: String
    var author: String
    var body: String
    var planVersion: Int?
    var createdAt: String?

    var id: String { commentID }

    enum CodingKeys: String, CodingKey {
        case author, body
        case commentID = "comment_id"
        case planVersion = "plan_version"
        case createdAt = "created_at"
    }
}

struct TaskListEnvelope: Decodable {
    let tasks: [ControlTask]
    var counts: [String: Int] = [:]
}

struct TaskDetailEnvelope: Decodable {
    let task: ControlTask
    var plans: [TaskPlan] = []
    var currentPlan: TaskPlan?
    var report: TaskReport?
    var comments: [TaskComment] = []

    enum CodingKeys: String, CodingKey {
        case task, plans, report, comments
        case currentPlan = "current_plan"
    }
}

struct ControlTaskEnvelope: Decodable { let task: ControlTask }
struct TaskPlansEnvelope: Decodable { let plans: [TaskPlan] }
struct TaskActivityEnvelope: Decodable {
    let activity: [TaskActivityEntry]
    let lastSequence: Int

    enum CodingKeys: String, CodingKey {
        case activity
        case lastSequence = "last_sequence"
    }
}
struct TaskApprovalsEnvelope: Decodable { let approvals: [TaskEffectApproval] }
struct TaskReportEnvelope: Decodable { let report: TaskReport }
struct TaskArtifactsEnvelope: Decodable { let artifacts: [TaskArtifact] }
struct TaskCandidatesEnvelope: Decodable { let candidates: [TaskCandidate] }
struct TaskCandidateDecisionEnvelope: Decodable {
    let candidate: TaskCandidate?
    let task: ControlTask?
}
struct TaskCommentEnvelope: Decodable {
    let comment: TaskComment
    let task: ControlTask
}
