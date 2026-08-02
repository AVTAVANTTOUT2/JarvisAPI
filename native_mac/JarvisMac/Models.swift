import Foundation
import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case today
    case chat
    case actions
    case memory
    case system

    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: "Aujourd’hui"
        case .chat: "Conversation"
        case .actions: "Actions"
        case .memory: "Mémoire"
        case .system: "Système"
        }
    }

    var symbol: String {
        switch self {
        case .today: "sparkles"
        case .chat: "bubble.left.and.bubble.right.fill"
        case .actions: "checkmark.circle.fill"
        case .memory: "brain.head.profile.fill"
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
