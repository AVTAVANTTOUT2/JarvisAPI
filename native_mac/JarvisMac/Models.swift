import Foundation
import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case today
    case chat
    /// Travaux confiés à JARVIS : plan, validation, exécution et résultat.
    case missions
    /// Liste personnelle simple : ajout, priorité et case à cocher.
    case todos
    case memory
    case terminal
    case system

    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: "Aujourd’hui"
        case .chat: "Conversation"
        case .missions: "Missions Jarvis"
        case .todos: "À faire"
        case .memory: "Mémoire"
        case .terminal: "Terminal"
        case .system: "Système"
        }
    }

    var sidebarHint: String? {
        switch self {
        case .missions: "Jarvis planifie et exécute"
        case .todos: "Votre liste à cocher"
        default: nil
        }
    }

    var symbol: String {
        switch self {
        case .today: "sparkles"
        case .chat: "bubble.left.and.bubble.right.fill"
        case .missions: "gearshape.2.fill"
        case .todos: "checkmark.circle.fill"
        case .memory: "brain.head.profile.fill"
        case .terminal: "terminal.fill"
        case .system: "waveform.path.ecg"
        }
    }
}

enum ConnectionPhase: Equatable {
    case checking
    case offline(String)
    case setupRequired
    case locked
    case ready

    var label: String {
        switch self {
        case .checking: "Connexion…"
        case .offline: "Cœur hors ligne"
        case .setupRequired: "Configuration requise"
        case .locked: "Verrouillé"
        case .ready: "Opérationnel"
        }
    }

    var color: Color {
        switch self {
        case .ready: .green
        case .checking: .blue
        case .locked, .setupRequired: .orange
        case .offline: .red
        }
    }
}

struct AuthStatus: Decodable {
    let configured: Bool
    let authenticated: Bool
    let csrfToken: String?
    let lockedOut: Bool?
    let lockoutSeconds: Int?
    let localRecoveryAvailable: Bool?
    let autoLockMinutes: Int?

    enum CodingKeys: String, CodingKey {
        case configured, authenticated
        case csrfToken = "csrf_token"
        case lockedOut = "locked_out"
        case lockoutSeconds = "lockout_seconds"
        case localRecoveryAvailable = "local_recovery_available"
        case autoLockMinutes = "auto_lock_minutes"
    }
}

struct AuthMutationResponse: Decodable {
    let ok: Bool
    let csrfToken: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case csrfToken = "csrf_token"
    }
}

struct TaskEnvelope: Decodable { let tasks: [JarvisTask] }

struct JarvisTask: Identifiable, Codable, Hashable {
    let id: Int
    var title: String
    var description: String?
    var priority: String
    var dueDate: String?
    var category: String?
    var status: String
    var createdAt: String?
    var completedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, priority, category, status
        case dueDate = "due_date"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }

    var isDone: Bool { status == "done" }

    var priorityLabel: String {
        switch priority {
        case "high": "Prioritaire"
        case "low": "Secondaire"
        default: "Normal"
        }
    }
}

struct TaskMutationEnvelope: Decodable { let task: JarvisTask }

struct NotificationEnvelope: Decodable { let notifications: [JarvisNotification] }

struct JarvisNotification: Identifiable, Codable, Hashable {
    let id: Int
    let source: String
    let title: String
    let content: String?
    let priority: String
    let read: Int?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, source, title, content, priority, read
        case createdAt = "created_at"
    }
}

struct CalendarEnvelope: Decodable { let events: [CalendarItem] }

struct CalendarItem: Identifiable, Codable, Hashable {
    let id: String
    let title: String
    let start: String
    let end: String?
    let location: String?
    let notes: String?
    let calendar: String?
}

struct ConversationEnvelope: Decodable { let conversations: [ConversationSummary] }

struct ConversationSummary: Identifiable, Codable, Hashable {
    let id: Int
    let title: String?
    let agent: String?
    let summary: String?
    let startedAt: String?
    let lastMessageAt: String?
    let lastMessage: String?
    let msgCount: Int?
    let pinned: Int?

    enum CodingKeys: String, CodingKey {
        case id, title, agent, summary, pinned
        case startedAt = "started_at"
        case lastMessageAt = "last_message_at"
        case lastMessage = "last_message"
        case msgCount = "msg_count"
    }

    var displayTitle: String {
        let value = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? "Nouvelle conversation" : value
    }
}

struct ConversationDetail: Decodable {
    let id: Int
    let title: String?
    let messages: [ConversationMessage]
}

struct ConversationMessage: Identifiable, Decodable {
    let id: Int
    let role: String
    let content: String
    let agent: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, role, content, agent
        case createdAt = "created_at"
    }
}

struct ChatMessage: Identifiable, Equatable {
    enum Role { case user, assistant, system }

    let id: UUID
    var role: Role
    var content: String
    var isStreaming: Bool
    var timestamp: Date

    init(
        id: UUID = UUID(),
        role: Role,
        content: String,
        isStreaming: Bool = false,
        timestamp: Date = .now
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.isStreaming = isStreaming
        self.timestamp = timestamp
    }
}

struct StatusResponse: Decodable {
    struct Models: Decodable { let fast: String?; let main: String? }
    struct Usage: Decodable {
        let msgCount: Int?
        let totalIn: Int?
        let totalOut: Int?
        let totalCost: Double?

        enum CodingKeys: String, CodingKey {
            case msgCount = "msg_count"
            case totalIn = "total_in"
            case totalOut = "total_out"
            case totalCost = "total_cost"
        }
    }
    struct Audio: Decodable {
        let sttAvailable: Bool?
        let sttEngine: String?
        let ttsAvailable: Bool?
        let ttsBackend: String?

        enum CodingKeys: String, CodingKey {
            case sttAvailable = "stt_available"
            case sttEngine = "stt_engine"
            case ttsAvailable = "tts_available"
            case ttsBackend = "tts_backend"
        }
    }
    struct Computer: Decodable { let available: Bool?; let shell: String? }
    struct EmailWatcher: Decodable {
        let running: Bool?
        let checkInterval: Int?
        let processedCount: Int?

        enum CodingKeys: String, CodingKey {
            case running
            case checkInterval = "check_interval"
            case processedCount = "processed_count"
        }
    }

    let user: String?
    let models: Models?
    let agentsRegistered: [String]?
    let today: Usage?
    let audio: Audio?
    let emailWatcher: EmailWatcher?
    let computer: Computer?

    enum CodingKeys: String, CodingKey {
        case user, models, today, audio, computer
        case agentsRegistered = "agents_registered"
        case emailWatcher = "email_watcher"
    }
}

struct AgenticRuntimeStatus: Decodable {
    let available: Bool
    let status: String
    let mode: String?
    let label: String?
    let activeRuns: Int
    let queuedRuns: Int
    let checkedAt: String?
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case available, healthy, ready, status, state, mode, label
        case activeRuns = "active_runs"
        case queuedRuns = "queued_runs"
        case checkedAt = "checked_at"
        case errorCode = "error_code"
    }

    init(
        available: Bool,
        status: String,
        mode: String? = nil,
        label: String? = nil,
        activeRuns: Int = 0,
        queuedRuns: Int = 0,
        checkedAt: String? = nil,
        errorCode: String? = nil
    ) {
        self.available = available
        self.status = status
        self.mode = mode
        self.label = label
        self.activeRuns = activeRuns
        self.queuedRuns = queuedRuns
        self.checkedAt = checkedAt
        self.errorCode = errorCode
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let rawStatus = try values.decodeIfPresent(String.self, forKey: .status)
            ?? values.decodeIfPresent(String.self, forKey: .state)
            ?? "unknown"
        status = rawStatus
        available = try values.decodeIfPresent(Bool.self, forKey: .available)
            ?? values.decodeIfPresent(Bool.self, forKey: .healthy)
            ?? values.decodeIfPresent(Bool.self, forKey: .ready)
            ?? ["available", "healthy", "ready", "running"].contains(rawStatus)
        mode = try values.decodeIfPresent(String.self, forKey: .mode)
        label = try values.decodeIfPresent(String.self, forKey: .label)
        activeRuns = try values.decodeIfPresent(Int.self, forKey: .activeRuns) ?? 0
        queuedRuns = try values.decodeIfPresent(Int.self, forKey: .queuedRuns) ?? 0
        checkedAt = try values.decodeIfPresent(String.self, forKey: .checkedAt)
        errorCode = try values.decodeIfPresent(String.self, forKey: .errorCode)
    }
}

struct AgenticRuntimeEnvelope: Decodable {
    let runtime: AgenticRuntimeStatus

    enum CodingKeys: String, CodingKey {
        case runtime, runtimes
        case agenticRuntime = "agentic_runtime"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        if let nested = try values.decodeIfPresent(AgenticRuntimeStatus.self, forKey: .runtime)
            ?? values.decodeIfPresent(AgenticRuntimeStatus.self, forKey: .agenticRuntime) {
            runtime = nested
        } else if let runtimes = try values.decodeIfPresent([AgenticRuntimeStatus].self, forKey: .runtimes) {
            runtime = runtimes.first(where: \.available)
                ?? runtimes.first
                ?? AgenticRuntimeStatus(available: false, status: "unavailable")
        } else {
            runtime = try AgenticRuntimeStatus(from: decoder)
        }
    }
}

struct AgenticStep: Identifiable, Decodable {
    let id: String
    let title: String
    let status: String
    let kind: String?
    let summary: String?
    let progress: Double?

    enum CodingKeys: String, CodingKey { case id, stepID = "step_id", title, name, status, kind, summary, progress }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
            ?? values.decodeIfPresent(String.self, forKey: .stepID)
            ?? UUID().uuidString
        title = try values.decodeIfPresent(String.self, forKey: .title)
            ?? values.decodeIfPresent(String.self, forKey: .name)
            ?? "Étape"
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "pending"
        kind = try values.decodeIfPresent(String.self, forKey: .kind)
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        progress = try values.decodeIfPresent(Double.self, forKey: .progress)
    }
}

struct AgenticApproval: Identifiable, Decodable {
    let id: String
    let title: String
    let summary: String?
    let status: String
    let riskLevel: String?
    let tool: String?
    let risks: [String]

    var isPending: Bool { status == "pending" }

    enum CodingKeys: String, CodingKey {
        case id, approvalID = "approval_id", title, action, summary, status, decision, tool, risks
        case riskLevel = "risk_level"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
            ?? values.decodeIfPresent(String.self, forKey: .approvalID)
            ?? UUID().uuidString
        title = try values.decodeIfPresent(String.self, forKey: .title)
            ?? values.decodeIfPresent(String.self, forKey: .action)
            ?? "Autorisation requise"
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        status = try values.decodeIfPresent(String.self, forKey: .decision)
            ?? values.decodeIfPresent(String.self, forKey: .status)
            ?? "pending"
        riskLevel = try values.decodeIfPresent(String.self, forKey: .riskLevel)
        tool = try values.decodeIfPresent(String.self, forKey: .tool)
        risks = try values.decodeIfPresent([String].self, forKey: .risks) ?? []
    }
}

/// Deliberately excludes event payloads so prompts and tool arguments cannot reach native views.
struct AgenticEvent: Identifiable, Decodable {
    let id: String
    let runID: String?
    let sequence: Int
    let type: String
    let timestamp: String?
    let level: String
    let visibility: String
    let sensitivity: String

    enum CodingKeys: String, CodingKey {
        case id, eventID = "event_id", runID = "run_id", sequence, type, timestamp, level, visibility, sensitivity
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let decodedRunID = try values.decodeIfPresent(String.self, forKey: .runID)
        let decodedSequence = try values.decodeIfPresent(Int.self, forKey: .sequence) ?? 0
        let decodedType = try values.decodeIfPresent(String.self, forKey: .type) ?? "agent.run.updated"
        id = try values.decodeIfPresent(String.self, forKey: .id)
            ?? values.decodeIfPresent(String.self, forKey: .eventID)
            ?? "\(decodedRunID ?? "run"):\(decodedSequence):\(decodedType)"
        runID = decodedRunID
        sequence = decodedSequence
        type = decodedType
        timestamp = try values.decodeIfPresent(String.self, forKey: .timestamp)
        level = try values.decodeIfPresent(String.self, forKey: .level) ?? "info"
        visibility = try values.decodeIfPresent(String.self, forKey: .visibility) ?? "user"
        sensitivity = try values.decodeIfPresent(String.self, forKey: .sensitivity) ?? "normal"
    }
}

struct AgenticArtifact: Identifiable, Decodable {
    let id: String
    let name: String
    let kind: String?
    let mimeType: String?
    let sizeBytes: Int?
    let url: String?
    let reference: String?

    enum CodingKeys: String, CodingKey {
        case id, artifactID = "artifact_id", name, filename, kind, type, url, reference
        case mimeType = "mime_type"
        case sizeBytes = "size_bytes"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
            ?? values.decodeIfPresent(String.self, forKey: .artifactID)
            ?? UUID().uuidString
        let decodedKind = try values.decodeIfPresent(String.self, forKey: .kind)
            ?? values.decodeIfPresent(String.self, forKey: .type)
        name = try values.decodeIfPresent(String.self, forKey: .name)
            ?? values.decodeIfPresent(String.self, forKey: .filename)
            ?? decodedKind
            ?? "Artefact"
        kind = decodedKind
        mimeType = try values.decodeIfPresent(String.self, forKey: .mimeType)
        sizeBytes = try values.decodeIfPresent(Int.self, forKey: .sizeBytes)
        url = try values.decodeIfPresent(String.self, forKey: .url)
        reference = try values.decodeIfPresent(String.self, forKey: .reference)
    }
}

struct AgenticRunError: Decodable {
    let code: String?
    let category: String?
    let message: String
    let retryable: Bool?
}

struct AgenticResultSummary: Decodable {
    let text: String?

    enum CodingKeys: String, CodingKey { case summary, message, text, content }

    init(from decoder: Decoder) throws {
        if let single = try? decoder.singleValueContainer(), let value = try? single.decode(String.self) {
            text = value
            return
        }
        let values = try decoder.container(keyedBy: CodingKeys.self)
        text = (try? values.decodeIfPresent(String.self, forKey: .summary))
            ?? (try? values.decodeIfPresent(String.self, forKey: .message))
            ?? (try? values.decodeIfPresent(String.self, forKey: .text))
            ?? (try? values.decodeIfPresent(String.self, forKey: .content))
    }
}

struct AgenticRun: Identifiable, Decodable {
    let id: String
    let title: String
    let status: String
    let phase: String?
    let progress: Double?
    let summary: String?
    let channel: String?
    let category: String?
    let runtimeID: String?
    let taskID: String?
    let conversationID: String?
    let requiresAttention: Bool
    let createdAt: String?
    let startedAt: String?
    let finishedAt: String?
    let completedAt: String?
    let updatedAt: String?
    let plan: [String]
    let steps: [AgenticStep]
    let approvals: [AgenticApproval]
    let artifacts: [AgenticArtifact]
    let result: AgenticResultSummary?
    let verification: AgenticResultSummary?
    let error: AgenticRunError?

    enum CodingKeys: String, CodingKey {
        case id, runID = "run_id", title, status, phase, progress, summary, channel, category, plan, steps
        case approvals, artifacts, result, verification, error
        case runtimeID = "runtime_id"
        case taskID = "task_id"
        case conversationID = "conversation_id"
        case requiresAttention = "requires_attention"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case completedAt = "completed_at"
        case updatedAt = "updated_at"
        case errorMessage = "error_message"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
            ?? values.decodeIfPresent(String.self, forKey: .runID)
            ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? "Tâche agentique"
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "created"
        phase = try values.decodeIfPresent(String.self, forKey: .phase)
        progress = try values.decodeIfPresent(Double.self, forKey: .progress)
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        channel = try values.decodeIfPresent(String.self, forKey: .channel)
        category = try values.decodeIfPresent(String.self, forKey: .category)
        runtimeID = try values.decodeIfPresent(String.self, forKey: .runtimeID)
        taskID = try values.decodeIfPresent(String.self, forKey: .taskID)
        conversationID = try values.decodeIfPresent(String.self, forKey: .conversationID)
        requiresAttention = try values.decodeIfPresent(Bool.self, forKey: .requiresAttention) ?? false
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        startedAt = try values.decodeIfPresent(String.self, forKey: .startedAt)
        finishedAt = try values.decodeIfPresent(String.self, forKey: .finishedAt)
        completedAt = try values.decodeIfPresent(String.self, forKey: .completedAt)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        plan = (try? values.decodeIfPresent([String].self, forKey: .plan)) ?? []
        steps = try values.decodeIfPresent([AgenticStep].self, forKey: .steps) ?? []
        approvals = try values.decodeIfPresent([AgenticApproval].self, forKey: .approvals) ?? []
        artifacts = try values.decodeIfPresent([AgenticArtifact].self, forKey: .artifacts) ?? []
        result = try? values.decodeIfPresent(AgenticResultSummary.self, forKey: .result)
        verification = try? values.decodeIfPresent(AgenticResultSummary.self, forKey: .verification)
        if let structured = try? values.decodeIfPresent(AgenticRunError.self, forKey: .error) {
            error = structured
        } else if let message = try values.decodeIfPresent(String.self, forKey: .errorMessage) {
            error = AgenticRunError(code: nil, category: nil, message: message, retryable: nil)
        } else {
            error = nil
        }
    }
}

struct AgenticRunsEnvelope: Decodable {
    let runs: [AgenticRun]

    enum CodingKeys: String, CodingKey { case runs, items, results }

    init(from decoder: Decoder) throws {
        if let single = try? decoder.singleValueContainer(), let direct = try? single.decode([AgenticRun].self) {
            runs = direct
            return
        }
        let values = try decoder.container(keyedBy: CodingKeys.self)
        runs = try values.decodeIfPresent([AgenticRun].self, forKey: .runs)
            ?? values.decodeIfPresent([AgenticRun].self, forKey: .items)
            ?? values.decodeIfPresent([AgenticRun].self, forKey: .results)
            ?? []
    }
}

struct AgenticRunEnvelope: Decodable {
    let run: AgenticRun

    enum CodingKeys: String, CodingKey { case run }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        run = try values.decodeIfPresent(AgenticRun.self, forKey: .run) ?? AgenticRun(from: decoder)
    }
}

struct AgenticEventsEnvelope: Decodable {
    let events: [AgenticEvent]

    enum CodingKeys: String, CodingKey { case events }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let decoded = try values.decodeIfPresent([AgenticEvent].self, forKey: .events) ?? []
        events = decoded.filter { $0.visibility == "user" && !["secret", "private"].contains($0.sensitivity) }
    }
}

struct AgenticApprovalsEnvelope: Decodable {
    let approvals: [AgenticApproval]
}

struct AgenticArtifactsEnvelope: Decodable {
    let artifacts: [AgenticArtifact]
}

struct AgenticRunCreateRequest: Encodable {
    let title: String
    let category = "direct_action"
    let origin = "user"
    let channel = "macos"
    let runID: String?

    enum CodingKeys: String, CodingKey {
        case title, category, origin, channel
        case runID = "run_id"
    }
}

struct AgenticMutationResponse: Decodable {
    init(from decoder: Decoder) throws { _ = decoder }
}

struct IntegrationsResponse: Decodable {
    struct CalendarState: Decodable { let available: Bool?; let error: String? }
    struct AvailableState: Decodable { let available: Bool? }

    let mail: Bool?
    let calendar: CalendarState?
    let weather: Bool?
    let imessage: Bool?
    let emailWatcher: Bool?
    let locationTracking: Bool?
    let computer: AvailableState?
    let audioDaemon: AvailableState?

    enum CodingKeys: String, CodingKey {
        case mail, calendar, weather, imessage, computer
        case emailWatcher = "email_watcher"
        case locationTracking = "location_tracking"
        case audioDaemon = "audio_daemon"
    }
}

struct BriefingResponse: Decodable { let kind: String; let content: String }
struct OKResponse: Decodable { let ok: Bool }

struct DashboardSnapshot {
    var tasks: [JarvisTask] = []
    var notifications: [JarvisNotification] = []
    var calendar: [CalendarItem] = []
    var conversations: [ConversationSummary] = []
    var status: StatusResponse?
    var integrations: IntegrationsResponse?
    var refreshedAt: Date?
}

extension String {
    var jarvisDate: Date? {
        let withFractional = ISO8601DateFormatter()
        withFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFractional.date(from: self) { return date }
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: self) { return date }
        let sql = DateFormatter()
        sql.locale = Locale(identifier: "en_US_POSIX")
        sql.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return sql.date(from: self)
    }
}
