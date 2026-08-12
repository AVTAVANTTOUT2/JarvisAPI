import AppKit
import Foundation
import SwiftUI

struct SystemView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top) {
                    SectionHeader(
                        eyebrow: "ÉTAT DU SYSTÈME",
                        title: "Jarvis Pulse",
                        subtitle: "Une lecture claire des capacités réellement disponibles."
                    )
                    Spacer()
                    JarvisOrb(size: 64, active: model.socket.isConnected)
                }
                heroStatus
                integrationsGrid
                agenticActivity
                diagnostics
            }
            .padding(28)
        }
        .navigationTitle("Système")
    }

    private var heroStatus: some View {
        HStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                StatusPill(text: model.phase.label, color: model.phase.color)
                Text(model.socket.isConnected ? "Le cœur et le canal temps réel répondent." : "Le cœur répond, le canal conversationnel se reconnecte.")
                    .font(.title2.weight(.semibold))
                Text("API \(model.api.baseURLString)")
                    .font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 5) {
                Text("\(model.snapshot.status?.today?.msgCount ?? 0)").font(.system(size: 34, weight: .semibold, design: .rounded))
                Text("messages aujourd’hui").font(.caption).foregroundStyle(.secondary)
            }
            Divider().frame(height: 48)
            VStack(alignment: .trailing, spacing: 5) {
                Text(cost).font(.system(size: 25, weight: .semibold, design: .rounded))
                Text("coût aujourd’hui").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(20)
        .jarvisGlass()
    }

    private var integrationsGrid: some View {
        VStack(alignment: .leading, spacing: 13) {
            Text("Capacités").font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), spacing: 12)], spacing: 12) {
                capability("Conversation", "WebSocket", "bubble.left.and.bubble.right.fill", model.socket.isConnected)
                capability("Microphone", model.snapshot.status?.audio?.sttEngine ?? "STT", "waveform", model.snapshot.status?.audio?.sttAvailable == true)
                capability("Voix", model.snapshot.status?.audio?.ttsBackend ?? "TTS", "speaker.wave.3.fill", model.snapshot.status?.audio?.ttsAvailable == true)
                capability("Mail", "Apple Mail", "envelope.fill", model.snapshot.integrations?.mail == true)
                capability("Calendrier", "Calendar.app", "calendar", model.snapshot.integrations?.calendar?.available == true)
                capability("Messages", "iMessage", "message.fill", model.snapshot.integrations?.imessage == true)
                capability("Météo", "Contexte local", "cloud.sun.fill", model.snapshot.integrations?.weather == true)
                capability("Contrôle Mac", model.snapshot.status?.computer?.shell ?? "Shell", "macbook.and.iphone", model.snapshot.status?.computer?.available == true)
            }
        }
    }

    private func capability(_ title: String, _ subtitle: String, _ symbol: String, _ available: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(available ? JarvisPalette.cyan : .secondary)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Circle().fill(available ? .green : .gray.opacity(0.55)).frame(width: 8, height: 8)
        }
        .padding(14)
        .jarvisGlass(cornerRadius: 16)
    }

    private var agenticActivity: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack {
                Label("Activité agentique", systemImage: "point.3.connected.trianglepath.dotted")
                    .font(.headline)
                Spacer()
                StatusPill(
                    text: model.agenticRuntime?.available == true ? "Disponible" : "Indisponible",
                    color: model.agenticRuntime?.available == true ? .green : .orange
                )
            }
            HStack(spacing: 18) {
                Label("\(model.agenticRuntime?.activeRuns ?? 0) active(s)", systemImage: "bolt.fill")
                Label("\(model.agenticRuntime?.queuedRuns ?? 0) en file", systemImage: "clock.fill")
                Spacer()
                Text(model.agenticRuntime?.status ?? "inconnu")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            if model.agenticRuns.isEmpty {
                Text("Aucune tâche agentique récente.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(model.agenticRuns.prefix(3))) { run in
                    Button {
                        model.selectAgenticRun(run)
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: run.status == "completed" ? "checkmark.circle.fill" : "circle.dotted")
                                .foregroundStyle(run.status == "failed" ? .red : JarvisPalette.cyan)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(AgenticNativeDisplay.safeText(run.title))
                                    .font(.subheadline.weight(.medium))
                                    .lineLimit(1)
                                Text(run.phase ?? run.status).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if run.requiresAttention {
                                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                            }
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.vertical, 3)
                }
                if let selected = model.selectedAgenticRun {
                    AgenticRunDetailView(run: selected)
                }
            }
        }
        .padding(18)
        .jarvisGlass()
    }

    private var diagnostics: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("Contrôle", systemImage: "wrench.and.screwdriver.fill").font(.headline)
                Spacer()
                if let refreshed = model.snapshot.refreshedAt {
                    Text("Actualisé \(refreshed.formatted(date: .omitted, time: .shortened))")
                        .font(.caption).foregroundStyle(.tertiary)
                }
            }
            HStack(spacing: 10) {
                Button("Reconnecter") { model.connectSocket() }.buttonStyle(.borderedProminent)
                Button("Actualiser les capacités") { Task { await model.refresh() } }
                    .buttonStyle(JarvisSecondaryButtonStyle())
                Button("Réglages") { openSettings() }
                    .buttonStyle(JarvisSecondaryButtonStyle())
                Button("Ouvrir le projet") { JarvisCoreLauncher.revealProject() }
                    .buttonStyle(JarvisSecondaryButtonStyle())
                Spacer()
                Button { Task { await model.logout() } } label: {
                    Label("Verrouiller", systemImage: "lock.fill").foregroundStyle(.red)
                }
                .buttonStyle(JarvisSecondaryButtonStyle())
            }
        }
        .padding(18)
        .jarvisGlass()
    }

    private var cost: String {
        let value = model.snapshot.status?.today?.totalCost ?? 0
        return value.formatted(.currency(code: "USD").precision(.fractionLength(3)))
    }
}

/// Native detail surface for one long-running task. Raw event payloads never enter this type.
struct AgenticRunDetailView: View {
    @EnvironmentObject private var model: AppModel
    let run: AgenticRun

    private var approvals: [AgenticApproval] {
        model.selectedAgenticApprovals.isEmpty ? run.approvals : model.selectedAgenticApprovals
    }

    private var artifacts: [AgenticArtifact] {
        model.selectedAgenticArtifacts.isEmpty ? run.artifacts : model.selectedAgenticArtifacts
    }

    private var normalizedStatus: String { run.status.lowercased() }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Détail de la tâche").font(.headline)
                    Text(AgenticNativeDisplay.safeText(run.title))
                        .font(.title3.weight(.semibold))
                    Text("\(run.phase ?? run.status) · \(AgenticNativeDisplay.duration(run))")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if run.requiresAttention {
                    StatusPill(text: "Action requise", color: .orange)
                }
            }

            if let progress = run.progress {
                ProgressView(value: normalizedProgress(progress))
                Text("Progression \(Int(normalizedProgress(progress) * 100)) %")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let summary = run.summary {
                Text(AgenticNativeDisplay.safeText(summary)).font(.subheadline)
            }
            if model.isAgenticDetailLoading {
                ProgressView("Actualisation du détail…").controlSize(.small)
            }
            if let message = model.agenticActionMessage {
                Text(AgenticNativeDisplay.safeText(message))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            linkedNavigation
            detailSection("Plan") {
                let plan = AgenticNativeDisplay.plan(run: run, events: model.selectedAgenticEvents)
                if plan.isEmpty {
                    Text("Plan non communiqué.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(plan.enumerated()), id: \.offset) { index, item in
                        Text("\(index + 1). \(item)")
                    }
                }
            }
            detailSection("Étapes") {
                if run.steps.isEmpty {
                    Text("Les étapes sont consignées dans l’activité.").foregroundStyle(.secondary)
                } else {
                    ForEach(run.steps.prefix(20)) { step in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("• \(AgenticNativeDisplay.safeText(step.title)) — \(step.status)")
                            if let summary = step.summary {
                                Text(AgenticNativeDisplay.safeText(summary))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            detailSection("Activité") {
                let events = Array(model.selectedAgenticEvents.sorted { $0.sequence < $1.sequence }.suffix(12))
                if events.isEmpty {
                    Text("Aucune activité publiée.").foregroundStyle(.secondary)
                } else {
                    ForEach(events) { event in
                        Text("• \(AgenticNativeDisplay.eventLabel(event))")
                    }
                }
            }
            detailSection("Outils") {
                let events = Array(model.selectedAgenticEvents.filter {
                    $0.type == "agent.tool.started" || $0.type == "agent.tool.completed"
                }.suffix(8))
                if events.isEmpty {
                    Text("Aucun outil signalé.").foregroundStyle(.secondary)
                } else {
                    ForEach(events) { event in
                        Text("• \(AgenticNativeDisplay.eventLabel(event))")
                    }
                }
                Text("Les noms, arguments et sorties d’outils restent masqués.")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            detailSection("Approbations") {
                if approvals.isEmpty {
                    Text("Aucune approbation.").foregroundStyle(.secondary)
                } else {
                    ForEach(approvals.prefix(12)) { approval in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(AgenticNativeDisplay.safeText(approval.title))
                                .fontWeight(.semibold)
                            if let summary = approval.summary {
                                Text(AgenticNativeDisplay.safeText(summary)).font(.caption)
                            }
                            Text(approval.status + (approval.risks.isEmpty ? "" : " · risque signalé"))
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                            if approval.isPending {
                                HStack {
                                    Button("Autoriser") {
                                        Task {
                                            await model.decideAgenticApproval(
                                                run: run,
                                                approval: approval,
                                                approved: true
                                            )
                                        }
                                    }
                                    Button("Refuser") {
                                        Task {
                                            await model.decideAgenticApproval(
                                                run: run,
                                                approval: approval,
                                                approved: false
                                            )
                                        }
                                    }
                                }
                                .buttonStyle(.bordered)
                                .disabled(model.isAgenticActionInFlight)
                            }
                        }
                    }
                }
            }
            if let error = run.error {
                detailSection("Erreur") {
                    Text(AgenticNativeDisplay.safeText(error.message, fallback: "Erreur sans détail exposable"))
                        .foregroundStyle(.red)
                    if let code = error.code {
                        Text(AgenticNativeDisplay.safeText(code)).font(.caption.monospaced())
                    }
                }
            }
            detailSection("Artefacts") {
                if artifacts.isEmpty {
                    Text("Aucun artefact.").foregroundStyle(.secondary)
                } else {
                    ForEach(artifacts.prefix(20)) { artifact in
                        Text("• \(AgenticNativeDisplay.artifactLabel(artifact))")
                    }
                    Text("Les références et chemins restent masqués.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            detailSection("Résultat") {
                Text(AgenticNativeDisplay.result(run) ?? "Aucun résultat final.")
            }
            controls
        }
        .font(.subheadline)
        .padding(16)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    @ViewBuilder
    private var linkedNavigation: some View {
        if run.taskID != nil || run.conversationID != nil {
            detailSection("Liens") {
                if let taskID = run.taskID {
                    Button("Ouvrir la tâche liée") { model.openAgenticTask(taskID) }
                }
                if let conversationID = run.conversationID {
                    Button("Ouvrir la conversation liée") {
                        model.openAgenticConversation(conversationID)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        detailSection("Contrôles") {
            HStack {
                if ["planning", "running", "executing", "verifying"].contains(normalizedStatus) {
                    Button("Mettre en pause") { Task { await model.pauseAgenticRun(run) } }
                }
                if normalizedStatus == "paused" {
                    Button("Reprendre") { Task { await model.resumeAgenticRun(run) } }
                }
                if !["completed", "failed", "cancelled"].contains(normalizedStatus) {
                    Button("Annuler", role: .destructive) {
                        Task { await model.cancelAgenticRun(run) }
                    }
                }
            }
            .buttonStyle(.bordered)
            .disabled(model.isAgenticActionInFlight)
        }
    }

    private func detailSection<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            Text(title.uppercased())
                .font(.caption.weight(.bold))
                .foregroundStyle(.secondary)
            content()
        }
    }

    private func normalizedProgress(_ value: Double) -> Double {
        min(1, max(0, value > 1 ? value / 100 : value))
    }
}

enum AgenticNativeDisplay {
    static func safeText(_ value: String?, fallback: String = "Information masquée") -> String {
        guard var text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return fallback
        }
        let patterns = [
            (#"(?i)\b(api[_-]?key|token|secret|password|authorization|cookie)\b\s*[:=]\s*[^\s,;]+"#, "[secret masqué]"),
            (#"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"#, "[secret masqué]"),
            (#"\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{8,}\b"#, "[secret masqué]"),
            (#"\b[A-Za-z]:\\[^\s,;]+"#, "[chemin masqué]"),
            (#"(?<![:\p{L}\p{N}_])(?:~|/)(?:[^\s,;]+)"#, "[chemin masqué]"),
        ]
        for (pattern, replacement) in patterns {
            text = text.replacingOccurrences(
                of: pattern,
                with: replacement,
                options: .regularExpression
            )
        }
        text = text.replacingOccurrences(of: #"[\p{Cc}\p{Cf}]+"#, with: " ", options: .regularExpression)
        text = text.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        return String(text.prefix(240))
    }

    static func duration(_ run: AgenticRun, now: Date = .now) -> String {
        guard let start = parseDate(run.startedAt ?? run.createdAt) else { return "Durée indisponible" }
        let end = parseDate(run.finishedAt ?? run.completedAt) ?? now
        let seconds = max(0, Int(end.timeIntervalSince(start)))
        let hours = seconds / 3_600
        let minutes = (seconds % 3_600) / 60
        let remainder = seconds % 60
        if hours > 0 { return "\(hours) h \(minutes) min" }
        if minutes > 0 { return "\(minutes) min \(remainder) s" }
        return "\(remainder) s"
    }

    static func eventLabel(_ event: AgenticEvent) -> String {
        [
            "agent.run.created": "Tâche créée",
            "agent.run.started": "Exécution démarrée",
            "agent.run.phase_changed": "Phase mise à jour",
            "agent.step.started": "Étape démarrée",
            "agent.step.completed": "Étape terminée",
            "agent.tool.started": "Outil interne démarré",
            "agent.tool.completed": "Outil interne terminé",
            "agent.approval.requested": "Autorisation demandée",
            "agent.approval.resolved": "Autorisation traitée",
            "agent.run.paused": "Tâche mise en pause",
            "agent.run.resumed": "Tâche reprise",
            "agent.run.verifying": "Résultat en vérification",
            "agent.run.completed": "Tâche terminée",
            "agent.run.failed": "Échec de la tâche",
            "agent.run.cancelled": "Tâche annulée",
        ][event.type] ?? "Activité de la tâche"
    }

    static func plan(run: AgenticRun, events: [AgenticEvent]) -> [String] {
        let explicit = run.plan.map { safeText($0) }.filter { !$0.isEmpty }
        if !explicit.isEmpty { return Array(explicit.prefix(12)) }
        var seen = Set<String>()
        return events
            .filter { ["agent.run.phase_changed", "agent.step.started", "agent.step.completed"].contains($0.type) }
            .map(eventLabel)
            .filter { seen.insert($0).inserted }
    }

    static func result(_ run: AgenticRun) -> String? {
        guard let value = run.result?.text ?? run.verification?.text else { return nil }
        return safeText(value)
    }

    static func artifactLabel(_ artifact: AgenticArtifact) -> String {
        let type = safeText(artifact.kind ?? artifact.mimeType, fallback: "artefact")
        guard let bytes = artifact.sizeBytes else { return type }
        if bytes >= 1_048_576 { return "\(type) · \(bytes / 1_048_576) Mo" }
        if bytes >= 1_024 { return "\(type) · \(bytes / 1_024) Ko" }
        return "\(type) · \(bytes) octets"
    }

    private static func parseDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}
