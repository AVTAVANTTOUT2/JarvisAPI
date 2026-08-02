import AppKit
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow
    @State private var prompt = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 11) {
                JarvisOrb(size: 38, active: model.isReady)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Jarvis").font(.headline)
                    Text(model.phase.label).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Circle().fill(model.phase.color).frame(width: 8, height: 8)
            }

            if model.isReady {
                HStack {
                    TextField("Demander rapidement…", text: $prompt)
                        .textFieldStyle(.plain)
                        .onSubmit { send() }
                    Button(action: send) { Image(systemName: "arrow.up.circle.fill") }
                        .buttonStyle(.plain)
                        .foregroundStyle(JarvisPalette.blue)
                }
                .padding(10)
                .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))

                HStack {
                    Label("\(model.snapshot.tasks.count) actions", systemImage: "checklist")
                    Spacer()
                    Label("\(model.snapshot.notifications.count) signaux", systemImage: "bell")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Divider()
            Button("Ouvrir Jarvis") {
                NSApplication.shared.activate(ignoringOtherApps: true)
                model.selectedSection = .today
            }
            Button("Nouvelle conversation") {
                NSApplication.shared.activate(ignoringOtherApps: true)
                model.newConversation()
            }
            Button("Afficher Jarvis Glance") { openWindow(id: "glance") }
            Divider()
            Button("Quitter Jarvis") { NSApplication.shared.terminate(nil) }
        }
        .padding(14)
        .frame(width: 300)
    }

    private func send() {
        let value = prompt
        prompt = ""
        NSApplication.shared.activate(ignoringOtherApps: true)
        model.selectedSection = .chat
        model.sendChat(value)
    }
}
