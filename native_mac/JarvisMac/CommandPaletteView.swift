import SwiftUI

struct CommandPaletteView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openWindow) private var openWindow
    @State private var query = ""
    @FocusState private var isFocused: Bool

    private var commands: [PaletteCommand] {
        let all: [PaletteCommand] = [
            .init(title: "Ouvrir Aujourd’hui", subtitle: "Votre synthèse calme et priorisée", symbol: "sparkles") {
                model.selectedSection = .today
            },
            .init(title: "Nouvelle conversation", subtitle: "Parler au cerveau Jarvis existant", symbol: "bubble.left.and.bubble.right.fill") {
                model.newConversation()
            },
            .init(title: "Afficher Jarvis Glance", subtitle: "Le compagnon flottant sur tous les bureaux", symbol: "rectangle.on.rectangle.angled") {
                openWindow(id: "glance")
            },
            .init(title: "Créer une action", subtitle: "Ouvrir la liste des tâches", symbol: "checkmark.circle.fill") {
                model.selectedSection = .actions
            },
            .init(title: "Préparer mon briefing", subtitle: "Agenda, tâches et signaux importants", symbol: "sun.max.fill") {
                Task { await model.generateBriefing() }
                model.selectedSection = .today
            },
            .init(title: "Vérifier les capacités", subtitle: "Audio, Mail, Calendar, Messages et agents", symbol: "waveform.path.ecg") {
                model.selectedSection = .system
            },
        ]
        guard !query.isEmpty else { return all }
        return all.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.subtitle.localizedCaseInsensitiveContains(query)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "sparkle.magnifyingglass")
                    .font(.title2).foregroundStyle(JarvisPalette.cyan)
                TextField("Chercher une commande ou poser une question…", text: $query)
                    .textFieldStyle(.plain)
                    .font(.title3)
                    .focused($isFocused)
                    .onSubmit { submitQuery() }
                Text("ESC").font(.caption2).foregroundStyle(.tertiary)
            }
            .padding(18)
            Divider().opacity(0.5)

            ScrollView {
                LazyVStack(spacing: 5) {
                    ForEach(commands) { command in
                        Button {
                            command.action()
                            dismiss()
                        } label: {
                            HStack(spacing: 13) {
                                Image(systemName: command.symbol)
                                    .font(.title3)
                                    .foregroundStyle(JarvisPalette.cyan)
                                    .frame(width: 30)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(command.title).font(.headline)
                                    Text(command.subtitle).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "return").font(.caption).foregroundStyle(.tertiary)
                            }
                            .padding(12)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .background(.white.opacity(0.001), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding(10)
            }
            .frame(maxHeight: 390)

            Divider().opacity(0.5)
            HStack {
                StatusPill(text: model.socket.isConnected ? "Jarvis en direct" : "Reconnexion", color: model.socket.isConnected ? .green : .orange)
                Spacer()
                Text("Entrée pour envoyer comme question").font(.caption).foregroundStyle(.tertiary)
            }
            .padding(12)
        }
        .frame(width: 590)
        .jarvisGlass(cornerRadius: 24)
        .padding(8)
        .onAppear { isFocused = true }
    }

    private func submitQuery() {
        let value = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        model.selectedSection = .chat
        model.sendChat(value)
        dismiss()
    }
}

private struct PaletteCommand: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let symbol: String
    let action: () -> Void
}
