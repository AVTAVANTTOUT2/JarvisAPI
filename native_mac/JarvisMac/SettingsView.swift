import AppKit
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("jarvis.baseURL") private var baseURL = JarvisAPI.defaultBaseURL
    @AppStorage("jarvis.projectRoot") private var projectRoot = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("JARVIS").path
    @State private var saved = false

    var body: some View {
        Form {
            Section("Connexion") {
                TextField("Adresse du cœur", text: $baseURL)
                    .textFieldStyle(.roundedBorder)
                Text("Le prototype utilise le backend FastAPI existant. Aucun agent ni aucune mémoire n’est embarqué dans l’interface.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Projet local") {
                TextField("Dossier JARVIS", text: $projectRoot)
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Button("Choisir…") { chooseProjectRoot() }
                    Button("Afficher dans le Finder") {
                        JarvisCoreLauncher.projectRoot = projectRoot
                        JarvisCoreLauncher.revealProject()
                    }
                }
            }
            Section("Intégration macOS") {
                LabeledContent("Barre des menus", value: "Active")
                LabeledContent("Jarvis Glance", value: "Disponible")
                LabeledContent("Raccourci global", value: "⇧⌘J")
                Text("Le widget WidgetKit est embarqué dans l’app. Après installation, ajoutez « Jarvis Glance » depuis la galerie de widgets macOS.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                if saved { Label("Enregistré", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                Button("Enregistrer et reconnecter") { save() }.buttonStyle(.borderedProminent)
            }
        }
        .formStyle(.grouped)
        .padding(12)
    }

    private func save() {
        model.api.setBaseURL(baseURL)
        JarvisCoreLauncher.projectRoot = projectRoot
        saved = true
        Task {
            await model.bootstrap()
            try? await Task.sleep(for: .seconds(2))
            saved = false
        }
    }

    private func chooseProjectRoot() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { projectRoot = url.path }
    }
}
