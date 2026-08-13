import AppKit
import Foundation
import LocalAuthentication

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
    @Published private(set) var agenticRuntime: AgenticRuntimeStatus?
    @Published private(set) var agenticRuns: [AgenticRun] = []
    @Published private(set) var selectedAgenticRun: AgenticRun?
    @Published private(set) var selectedAgenticEvents: [AgenticEvent] = []
    @Published private(set) var selectedAgenticApprovals: [AgenticApproval] = []
    @Published private(set) var selectedAgenticArtifacts: [AgenticArtifact] = []
    @Published private(set) var isAgenticDetailLoading = false
    @Published private(set) var isAgenticActionInFlight = false
    @Published private(set) var agenticActionMessage: String?
    @Published var errorMessage: String?
    @Published var briefing: String?
    @Published var isCommandPalettePresented = false

    let api = JarvisAPI()
    let socket = JarvisSocket()
    let audio = NativeAudioService()
    /// La session SSH vit ici et non dans la vue : changer de section ne doit
    /// pas couper un shell distant.
    let terminal = TerminalBridge()

    private var streamingMessageID: UUID?
    private var agenticRefreshTask: Task<Void, Never>?
    private var agenticDetailTask: Task<Void, Never>?
    private var notifiedIDs = Set<Int>()
    private let biometricCredentials = BiometricCredentialStore()

    var userName: String { snapshot.status?.user ?? "Monsieur" }
    var isReady: Bool { phase == .ready }
    var biometricUnlockAvailable: Bool { biometricCredentials.isAvailable }
    var biometricUnlockLabel: String { biometricCredentials.label }

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
            } else if biometricCredentials.isAvailable {
                // Le cookie serveur peut encore être valide, mais l’interface
                // native reste protégée à chaque nouveau lancement.
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
            try? biometricCredentials.save(secret: secret)
            phase = .ready
            errorMessage = nil
            await readySession()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func unlockWithBiometrics() async -> Bool {
        guard biometricCredentials.isAvailable else { return false }
        do {
            let secret = try await biometricCredentials.retrieve()
            try await api.unlock(secret: secret)
            phase = .ready
            errorMessage = nil
            await readySession()
            return true
        } catch {
            // Un secret modifié depuis une autre interface ne doit jamais
            // provoquer une boucle biométrique avec une valeur obsolète.
            if !(error is LAError) {
                biometricCredentials.delete()
            }
            errorMessage = error.localizedDescription
            return false
        }
    }

    func logout() async {
        try? await api.logout()
        biometricCredentials.delete()
        socket.disconnect()
        // Un shell distant laissé ouvert derrière l'écran verrouillé annulerait
        // le verrou : la session SSH tombe avec la session applicative.
        terminal.teardown()
        agenticRefreshTask?.cancel()
        agenticDetailTask?.cancel()
        phase = .locked
        snapshot = DashboardSnapshot()
        chatMessages = []
        agenticRuntime = nil
        agenticRuns = []
        selectedAgenticRun = nil
        selectedAgenticEvents = []
        selectedAgenticApprovals = []
        selectedAgenticArtifacts = []
        agenticActionMessage = nil
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
            if let runtime = try? await api.agenticRuntimeStatus() {
                agenticRuntime = runtime
            }
            if let runs = try? await api.agenticRuns() {
                agenticRuns = runs
            }
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
        case "terminal": selectedSection = .terminal
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
        case _ where type.hasPrefix("agent."):
            handleAgenticEvent(type)
        default:
            break
        }
    }

    func selectAgenticRun(_ run: AgenticRun) {
        selectedAgenticRun = run
        selectedAgenticEvents = []
        selectedAgenticApprovals = run.approvals
        selectedAgenticArtifacts = run.artifacts
        agenticActionMessage = nil
        agenticDetailTask?.cancel()
        agenticDetailTask = Task { [weak self] in
            await self?.refreshAgenticRunDetail(id: run.id)
        }
    }

    func refreshAgenticRunDetail(id: String) async {
        isAgenticDetailLoading = true
        defer {
            if selectedAgenticRun?.id == id {
                isAgenticDetailLoading = false
            }
        }
        do {
            let run = try await api.agenticRun(id: id)
            let events = try? await api.agenticRunEvents(id: id)
            let approvals = try? await api.agenticRunApprovals(id: id)
            let artifacts = try? await api.agenticRunArtifacts(id: id)
            guard !Task.isCancelled, selectedAgenticRun?.id == id else { return }
            selectedAgenticRun = run
            selectedAgenticEvents = events ?? []
            selectedAgenticApprovals = approvals ?? run.approvals
            selectedAgenticArtifacts = artifacts ?? run.artifacts
            if events == nil || approvals == nil || artifacts == nil {
                agenticActionMessage = "Certaines informations sont temporairement indisponibles."
            }
            if let index = agenticRuns.firstIndex(where: { $0.id == run.id }) {
                agenticRuns[index] = run
            }
        } catch {
            guard selectedAgenticRun?.id == id else { return }
            agenticActionMessage = "Le détail de la tâche est temporairement indisponible."
        }
    }

    func pauseAgenticRun(_ run: AgenticRun) async {
        await performAgenticAction(runID: run.id, success: "Tâche mise en pause.") {
            try await api.pauseAgenticRun(id: run.id)
        }
    }

    func resumeAgenticRun(_ run: AgenticRun) async {
        await performAgenticAction(runID: run.id, success: "Tâche reprise.") {
            try await api.resumeAgenticRun(id: run.id)
        }
    }

    func cancelAgenticRun(_ run: AgenticRun) async {
        await performAgenticAction(runID: run.id, success: "Annulation demandée.") {
            try await api.cancelAgenticRun(id: run.id)
        }
    }

    func decideAgenticApproval(
        run: AgenticRun,
        approval: AgenticApproval,
        approved: Bool
    ) async {
        await performAgenticAction(runID: run.id, success: "Décision enregistrée.") {
            try await api.decideAgenticApproval(
                runID: run.id,
                approvalID: approval.id,
                approved: approved
            )
        }
    }

    func openAgenticTask(_ taskID: String) {
        guard !taskID.isEmpty else { return }
        selectedSection = .today
    }

    func openAgenticConversation(_ conversationID: String) {
        selectedSection = .chat
        guard let id = Int(conversationID),
              let summary = snapshot.conversations.first(where: { $0.id == id }) else { return }
        Task { await openConversation(summary) }
    }

    private func performAgenticAction(
        runID: String,
        success: String,
        operation: () async throws -> Void
    ) async {
        guard !isAgenticActionInFlight else { return }
        isAgenticActionInFlight = true
        defer { isAgenticActionInFlight = false }
        do {
            try await operation()
            agenticActionMessage = success
            await refreshAgenticRunDetail(id: runID)
            if let runs = try? await api.agenticRuns() {
                agenticRuns = runs
            }
            if let runtime = try? await api.agenticRuntimeStatus() {
                agenticRuntime = runtime
            }
        } catch {
            agenticActionMessage = "Action impossible. Réessayez."
        }
    }

    private func handleAgenticEvent(_ type: String) {
        let labels = [
            "agent.run.created": "Tâche créée",
            "agent.run.started": "Tâche en cours",
            "agent.run.phase_changed": "Étape suivante",
            "agent.tool.started": "Action en cours…",
            "agent.tool.completed": "Action terminée",
            "agent.approval.requested": "Autorisation requise",
            "agent.approval.resolved": "Décision enregistrée",
            "agent.run.paused": "Tâche en pause",
            "agent.run.resumed": "Tâche reprise",
            "agent.run.blocked": "Tâche bloquée",
            "agent.run.verifying": "Vérification du résultat",
            "agent.run.completed": "Tâche terminée",
            "agent.run.failed": "Échec de la tâche",
            "agent.run.cancelled": "Tâche annulée",
        ]
        chatStatus = labels[type] ?? "Mise à jour de la tâche"
        agenticRefreshTask?.cancel()
        agenticRefreshTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled, let self else { return }
            if let runtime = try? await self.api.agenticRuntimeStatus() {
                self.agenticRuntime = runtime
            }
            if let runs = try? await self.api.agenticRuns() {
                self.agenticRuns = runs
            }
            if let selectedID = self.selectedAgenticRun?.id {
                await self.refreshAgenticRunDetail(id: selectedID)
            }
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
