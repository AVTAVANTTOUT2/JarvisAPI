import AppKit
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
