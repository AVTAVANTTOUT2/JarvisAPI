import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    @State private var quickPrompt = ""

    private let columns = [
        GridItem(.flexible(minimum: 300), spacing: 16),
        GridItem(.flexible(minimum: 300), spacing: 16),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                hero
                metrics
                LazyVGrid(columns: columns, alignment: .leading, spacing: 16) {
                    focusCard
                    agendaCard
                    notificationsCard
                    pulseCard
                }
                if let briefing = model.briefing { briefingCard(briefing) }
            }
            .padding(28)
        }
        .scrollIndicators(.hidden)
        .navigationTitle("Aujourd’hui")
    }

    private var hero: some View {
        HStack(alignment: .center, spacing: 24) {
            VStack(alignment: .leading, spacing: 10) {
                Text(Date.now.formatted(.dateTime.weekday(.wide).day().month(.wide)).capitalized)
                    .font(.caption.weight(.semibold))
                    .tracking(0.8)
                    .foregroundStyle(JarvisPalette.cyan)
                Text("\(greeting), \(model.userName).")
                    .font(.system(size: 36, weight: .semibold, design: .rounded))
                    .tracking(-1.1)
                Text(summaryLine)
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            JarvisOrb(size: 76, active: model.isReady)
        }
        .padding(.horizontal, 3)
    }

    private var metrics: some View {
        HStack(spacing: 12) {
            metric(symbol: "checklist", value: "\(model.snapshot.tasks.count)", label: "éléments à faire", color: JarvisPalette.blue)
            metric(symbol: "bell.badge.fill", value: "\(model.snapshot.notifications.count)", label: "signaux à lire", color: .orange)
            metric(symbol: "calendar", value: "\(model.snapshot.calendar.count)", label: "événements proches", color: .purple)
            metric(
                symbol: "bolt.horizontal.circle.fill",
                value: model.socket.isConnected ? "LIVE" : "SYNC",
                label: "connexion au cœur",
                color: model.socket.isConnected ? .green : .orange
            )
        }
    }

    private func metric(symbol: String, value: String, label: String, color: Color) -> some View {
        HStack(spacing: 11) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(color)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 1) {
                Text(value).font(.headline.weight(.semibold))
                Text(label).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity)
        .jarvisGlass(cornerRadius: 16)
    }

    private var focusCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            cardHeader("Focus", symbol: "scope", action: "Tout voir") { model.selectedSection = .todos }
            if let task = model.snapshot.tasks.first {
                VStack(alignment: .leading, spacing: 11) {
                    StatusPill(text: task.priorityLabel, color: task.priority == "high" ? .orange : JarvisPalette.blue)
                    Text(task.title).font(.title2.weight(.semibold)).lineLimit(3)
                    if let description = task.description, !description.isEmpty {
                        Text(description).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                    }
                    HStack {
                        if let due = task.dueDate, !due.isEmpty {
                            Label(due, systemImage: "clock").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Terminer") { Task { await model.toggleTask(task) } }
                            .buttonStyle(.borderedProminent)
                    }
                }
            } else {
                EmptyState(symbol: "checkmark.seal", title: "Rien ne presse", subtitle: "Votre liste active est vide.")
                    .frame(minHeight: 150)
            }
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, minHeight: 260, alignment: .topLeading)
        .jarvisGlass()
    }

    private var agendaCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            cardHeader("Agenda", symbol: "calendar.day.timeline.left", action: "À faire") { model.selectedSection = .todos }
            if model.snapshot.calendar.isEmpty {
                EmptyState(symbol: "calendar.badge.checkmark", title: "Agenda dégagé", subtitle: "Aucun événement remonté pour les prochaines 48 heures.")
                    .frame(minHeight: 165)
            } else {
                ForEach(model.snapshot.calendar.prefix(4)) { event in
                    HStack(alignment: .top, spacing: 12) {
                        Text(eventTime(event.start))
                            .font(.system(.subheadline, design: .monospaced).weight(.semibold))
                            .foregroundStyle(JarvisPalette.cyan)
                            .frame(width: 48, alignment: .leading)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(event.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                            Text([event.calendar, event.location].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                    }
                    if event.id != model.snapshot.calendar.prefix(4).last?.id { Divider().opacity(0.5) }
                }
            }
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, minHeight: 260, alignment: .topLeading)
        .jarvisGlass()
    }

    private var notificationsCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            cardHeader("Signaux", symbol: "bell.and.waves.left.and.right", action: nil, handler: {})
            if model.snapshot.notifications.isEmpty {
                EmptyState(symbol: "bell.slash", title: "Tout est calme", subtitle: "Jarvis n’a rien d’important à signaler.")
                    .frame(minHeight: 160)
            } else {
                ForEach(model.snapshot.notifications.prefix(4)) { notification in
                    Button {
                        Task { await model.markNotificationRead(notification) }
                    } label: {
                        HStack(alignment: .top, spacing: 11) {
                            Circle()
                                .fill(notification.priority == "urgent" ? .red : notification.priority == "high" ? .orange : JarvisPalette.blue)
                                .frame(width: 8, height: 8)
                                .padding(.top, 5)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(notification.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                                if let content = notification.content {
                                    Text(content).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                }
                            }
                            Spacer()
                            Image(systemName: "checkmark.circle").foregroundStyle(.tertiary)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, minHeight: 250, alignment: .topLeading)
        .jarvisGlass()
    }

    private var pulseCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            cardHeader("Jarvis Pulse", symbol: "waveform.path.ecg", action: "Système") { model.selectedSection = .system }
            HStack(spacing: 16) {
                JarvisOrb(size: 54, active: model.socket.isConnected)
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.socket.isConnected ? "Toutes les fonctions essentielles répondent." : "Le canal temps réel se reconnecte.")
                        .font(.headline).lineLimit(2)
                    Text("\(model.snapshot.status?.agentsRegistered?.count ?? 0) agents · \(model.snapshot.status?.models?.main ?? "modèle local")")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Divider().opacity(0.5)
            HStack {
                Button("Briefing du matin") { Task { await model.generateBriefing() } }
                    .buttonStyle(.borderedProminent)
                Button("Parler à Jarvis") { model.selectedSection = .chat }
                    .buttonStyle(JarvisSecondaryButtonStyle())
                Spacer()
            }
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, minHeight: 250, alignment: .topLeading)
        .jarvisGlass()
    }

    private func briefingCard(_ briefing: String) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            cardHeader("Briefing", symbol: "sun.max.fill", action: "Fermer") { model.briefing = nil }
            Text(briefing)
                .font(.body)
                .textSelection(.enabled)
                .lineSpacing(4)
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .jarvisGlass()
    }

    private func cardHeader(_ title: String, symbol: String, action: String?, handler: @escaping () -> Void) -> some View {
        HStack {
            Label(title, systemImage: symbol).font(.headline)
            Spacer()
            if let action { Button(action, action: handler).buttonStyle(.plain).foregroundStyle(JarvisPalette.cyan) }
        }
    }

    private var greeting: String {
        switch Calendar.current.component(.hour, from: .now) {
        case 5..<12: "Bonjour"
        case 12..<18: "Bon après-midi"
        default: "Bonsoir"
        }
    }

    private var summaryLine: String {
        let tasks = model.snapshot.tasks.count
        let signals = model.snapshot.notifications.count
        if tasks == 0 && signals == 0 { return "Votre journée est sous contrôle." }
        return "\(tasks) élément\(tasks > 1 ? "s" : "") à faire et \(signals) signal\(signals > 1 ? "s" : "") méritent votre attention."
    }

    private func eventTime(_ value: String) -> String {
        guard let date = value.jarvisDate else { return String(value.prefix(5)) }
        return date.formatted(date: .omitted, time: .shortened)
    }
}
