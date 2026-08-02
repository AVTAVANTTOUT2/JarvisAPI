import AppKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var selectedSection: AppSection = .today
    @Published private(set) var phase: ConnectionPhase = .checking
    @Published private(set) var snapshot = DashboardSnapshot()
    @Published private(set) var chatMessages: [ChatMessage] = []
    @Published private(set) var activeConversationID: Int?
    @Published private(set) var isChatProcessing = false
    @Published private(set) var chatStatus = "Prêt"
    @Published private(set) var isRefreshing = false
    @Published var errorMessage: String?
    @Published var briefing: String?
    @Published var isCommandPalettePresented = false

    let api = JarvisAPI()
    let socket = JarvisSocket()
    let audio = NativeAudioService()

    private var streamingMessageID: UUID?
    private var notifiedIDs = Set<Int>()

    var userName: String { snapshot.status?.user ?? "Monsieur" }
    var isReady: Bool { phase == .ready }

    init() {
        socket.onEvent = { [weak self] event in self?.handleSocketEvent(event) }
        socket.onAudio = { [weak self] data in self?.audio.play(data) }
    }

    func bootstrap() async {
        if ProcessInfo.processInfo.environment["JARVIS_UI_PREVIEW"] == "1"
            || ProcessInfo.processInfo.arguments.contains("--ui-preview")
            || ProcessInfo.processInfo.arguments.contains("--export-ui-preview")
        {
            loadPreviewData()
            return
        }
        phase = .checking
        do {
            let auth = try await api.discoverAuthStatus()
            if !auth.configured {
                phase = .setupRequired
            } else if !auth.authenticated {
                phase = .locked
            } else {
                phase = .ready
                await readySession()
            }
        } catch {
            phase = .offline(error.localizedDescription)
        }
    }

    private func loadPreviewData() {
        phase = .ready
        snapshot.tasks = [
            JarvisTask(id: 1, title: "Finaliser le dossier de présentation", description: "Préparer une version courte et nette avant 17 h.", priority: "high", dueDate: "Aujourd’hui · 17:00", category: "Travail", status: "todo", createdAt: nil, completedAt: nil),
            JarvisTask(id: 2, title: "Répondre aux messages importants", description: nil, priority: "medium", dueDate: nil, category: "Personnel", status: "todo", createdAt: nil, completedAt: nil),
            JarvisTask(id: 3, title: "Planifier la séance de sport", description: nil, priority: "low", dueDate: "Demain", category: "Santé", status: "todo", createdAt: nil, completedAt: nil),
        ]
        snapshot.notifications = [
            JarvisNotification(id: 1, source: "mail", title: "Réponse attendue aujourd’hui", content: "Un message prioritaire est resté sans réponse depuis ce matin.", priority: "high", read: 0, createdAt: nil),
            JarvisNotification(id: 2, source: "system", title: "Sauvegarde terminée", content: "La mémoire locale a été sauvegardée correctement.", priority: "medium", read: 0, createdAt: nil),
        ]
        snapshot.calendar = [
            CalendarItem(id: "1", title: "Point projet", start: ISO8601DateFormatter().string(from: .now.addingTimeInterval(3_600)), end: nil, location: "Bureau", notes: nil, calendar: "Travail"),
            CalendarItem(id: "2", title: "Salle de sport", start: ISO8601DateFormatter().string(from: .now.addingTimeInterval(18_000)), end: nil, location: "Lille", notes: nil, calendar: "Personnel"),
        ]
        snapshot.conversations = [
            ConversationSummary(id: 1, title: "Organisation de la semaine", agent: "productivity", summary: nil, startedAt: nil, lastMessageAt: nil, lastMessage: "Voici les trois priorités que je retiens…", msgCount: 8, pinned: 1),
            ConversationSummary(id: 2, title: "Préparation du dossier", agent: "school", summary: nil, startedAt: nil, lastMessageAt: nil, lastMessage: "Le plan est cohérent, il reste à simplifier…", msgCount: 14, pinned: 0),
        ]
        snapshot.status = StatusResponse(
            user: "Nolann",
            models: .init(fast: "DeepSeek Fast", main: "DeepSeek Main"),
            agentsRegistered: ["info", "school", "productivity", "coach", "journal", "memory"],
            today: .init(msgCount: 24, totalIn: 0, totalOut: 0, totalCost: 0.018),
            audio: .init(sttAvailable: true, sttEngine: "WhisperKit", ttsAvailable: true, ttsBackend: "TTSKit"),
            emailWatcher: .init(running: true, checkInterval: 60, processedCount: 12),
            computer: .init(available: true, shell: "zsh")
        )
        snapshot.integrations = IntegrationsResponse(
            mail: true,
            calendar: .init(available: true, error: nil),
            weather: true,
            imessage: true,
            emailWatcher: true,
            locationTracking: true,
            computer: .init(available: true),
            audioDaemon: .init(available: true)
        )
        snapshot.refreshedAt = .now
    }

    func unlock(secret: String) async -> Bool {
        do {
            try await api.unlock(secret: secret, setup: phase == .setupRequired)
            phase = .ready
            errorMessage = nil
            await readySession()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func logout() async {
        try? await api.logout()
        socket.disconnect()
        phase = .locked
        snapshot = DashboardSnapshot()
        chatMessages = []
    }

    func retryConnection() async { await bootstrap() }

    func refresh() async {
        guard isReady else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        do {
            snapshot.tasks = try await api.tasks()
            snapshot.notifications = (try? await api.notifications()) ?? []
            snapshot.calendar = (try? await api.calendarToday()) ?? []
            snapshot.status = try? await api.status()
            snapshot.integrations = try? await api.integrations()
            snapshot.conversations = (try? await api.conversations()) ?? []
            snapshot.refreshedAt = .now
            deliverNewNativeNotifications()
            errorMessage = nil
        } catch let error as JarvisAPIError {
            if case .http(let status, _) = error, status == 401 {
                socket.disconnect()
                phase = .locked
            } else {
                errorMessage = error.localizedDescription
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createTask(title: String, priority: String = "medium") async -> Bool {
        guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        do {
            let task = try await api.createTask(title: title, priority: priority)
            snapshot.tasks.insert(task, at: 0)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func toggleTask(_ task: JarvisTask) async {
        do {
            let updated = try await api.updateTask(task, status: task.isDone ? "todo" : "done")
            if updated.isDone {
                snapshot.tasks.removeAll { $0.id == task.id }
            } else if let index = snapshot.tasks.firstIndex(where: { $0.id == task.id }) {
                snapshot.tasks[index] = updated
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func markNotificationRead(_ notification: JarvisNotification) async {
        do {
            try await api.markNotificationRead(notification.id)
            snapshot.notifications.removeAll { $0.id == notification.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func sendChat(_ text: String, speak: Bool = false) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        guard socket.isConnected else {
            errorMessage = "La conversation temps réel n’est pas connectée."
            connectSocket()
            return
        }
        chatMessages.append(ChatMessage(role: .user, content: clean))
        let assistant = ChatMessage(role: .assistant, content: "", isStreaming: true)
        streamingMessageID = assistant.id
        chatMessages.append(assistant)
        isChatProcessing = true
        chatStatus = "Jarvis réfléchit…"
        socket.sendText(clean, stream: true, tts: speak)
    }

    func newConversation() {
        chatMessages = []
        activeConversationID = nil
        streamingMessageID = nil
        socket.newConversation()
        selectedSection = .chat
    }

    func openConversation(_ summary: ConversationSummary) async {
        do {
            let detail = try await api.conversation(id: summary.id)
            chatMessages = detail.messages.map {
                ChatMessage(
                    role: $0.role == "user" ? .user : .assistant,
                    content: $0.content,
                    timestamp: $0.createdAt?.jarvisDate ?? .now
                )
            }
            activeConversationID = summary.id
            socket.switchConversation(summary.id)
            selectedSection = .chat
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggleVoiceRecording() async {
        if audio.isRecording {
            guard let data = audio.stopRecording(), !data.isEmpty else { return }
            chatMessages.append(ChatMessage(role: .user, content: "Message vocal", isStreaming: false))
            let assistant = ChatMessage(role: .assistant, content: "", isStreaming: true)
            streamingMessageID = assistant.id
            chatMessages.append(assistant)
            isChatProcessing = true
            chatStatus = "Transcription…"
            socket.sendAudio(data)
        } else if !socket.isConnected {
            errorMessage = "Connectez Jarvis avant de parler."
        } else {
            _ = await audio.startRecording()
        }
    }

    func generateBriefing() async {
        chatStatus = "Préparation du briefing…"
        do {
            briefing = try await api.briefing().content
            chatStatus = "Prêt"
        } catch {
            errorMessage = error.localizedDescription
            chatStatus = "Prêt"
        }
    }

    func connectSocket() {
        guard isReady else { return }
        do { socket.connect(request: try api.websocketRequest()) }
        catch { errorMessage = error.localizedDescription }
    }

    func openURL(_ url: URL) {
        guard url.scheme == "jarvis" else { return }
        switch url.host {
        case "chat": selectedSection = .chat
        case "actions": selectedSection = .actions
        case "system": selectedSection = .system
        default: selectedSection = .today
        }
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func readySession() async {
        await NativeNotifications.requestAuthorization()
        connectSocket()
        await refresh()
    }

    private func deliverNewNativeNotifications() {
        for notification in snapshot.notifications where !notifiedIDs.contains(notification.id) {
            notifiedIDs.insert(notification.id)
            NativeNotifications.deliver(notification)
        }
    }

    private func handleSocketEvent(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "connected", "conversation_switched":
            activeConversationID = event["conversation_id"] as? Int
            chatStatus = "Prêt"
        case "transcript":
            if let content = event["content"] as? String,
               let index = chatMessages.lastIndex(where: { $0.role == .user && $0.content == "Message vocal" }) {
                chatMessages[index].content = content
            }
        case "chunk":
            appendStream(event["content"] as? String ?? "")
        case "response", "response_followup":
            setStream(event["content"] as? String ?? "", final: true)
        case "response_clean":
            setStream(event["content"] as? String ?? "", final: false)
        case "done", "speech_done":
            finishStream()
        case "status":
            chatStatus = event["content"] as? String ?? "Jarvis travaille…"
        case "routing":
            chatStatus = "Routage intelligent…"
        case "processing":
            chatStatus = "Jarvis réfléchit…"
        case "error":
            let message = event["message"] as? String ?? "Erreur de conversation"
            if let id = streamingMessageID,
               let index = chatMessages.firstIndex(where: { $0.id == id }) {
                chatMessages[index].content = message
                chatMessages[index].role = .system
            } else {
                chatMessages.append(ChatMessage(role: .system, content: message))
            }
            finishStream()
        case "conversation_updated":
            Task { await refreshConversations() }
        case "action_pending":
            chatStatus = "Confirmation requise"
        default:
            break
        }
    }

    private func appendStream(_ content: String) {
        guard let id = streamingMessageID,
              let index = chatMessages.firstIndex(where: { $0.id == id }) else { return }
        chatMessages[index].content += content
        chatStatus = "Réponse en cours…"
    }

    private func setStream(_ content: String, final: Bool) {
        if let id = streamingMessageID,
           let index = chatMessages.firstIndex(where: { $0.id == id }) {
            chatMessages[index].content = content
            chatMessages[index].isStreaming = !final
        } else if !content.isEmpty {
            chatMessages.append(ChatMessage(role: .assistant, content: content))
        }
        if final { finishStream() }
    }

    private func finishStream() {
        if let id = streamingMessageID,
           let index = chatMessages.firstIndex(where: { $0.id == id }) {
            chatMessages[index].isStreaming = false
            if chatMessages[index].content.isEmpty { chatMessages.remove(at: index) }
        }
        streamingMessageID = nil
        isChatProcessing = false
        chatStatus = "Prêt"
    }

    private func refreshConversations() async {
        snapshot.conversations = (try? await api.conversations()) ?? snapshot.conversations
    }
}
