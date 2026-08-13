import SwiftUI

@main
struct JarvisMacApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .frame(minWidth: 980, minHeight: 680)
                .onOpenURL { model.openURL($0) }
        }
        .defaultSize(width: 1240, height: 820)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandMenu("Jarvis") {
                Button("Aujourd’hui") { model.selectedSection = .today }
                    .keyboardShortcut("1", modifiers: .command)
                Button("Conversation") { model.selectedSection = .chat }
                    .keyboardShortcut("2", modifiers: .command)
                Button("Actions") { model.selectedSection = .actions }
                    .keyboardShortcut("3", modifiers: .command)
                Button("Terminal") { model.selectedSection = .terminal }
                    .keyboardShortcut("4", modifiers: .command)
                Divider()
                Button("Demander à Jarvis…") { model.isCommandPalettePresented = true }
                    .keyboardShortcut("j", modifiers: [.command, .shift])
                Button("Actualiser") { Task { await model.refresh() } }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }

        MenuBarExtra {
            MenuBarView()
                .environmentObject(model)
        } label: {
            Label("Jarvis", systemImage: model.isReady ? "sparkles" : "circle.dashed")
        }
        .menuBarExtraStyle(.window)

        Window("Jarvis Glance", id: "glance") {
            DeskWidgetView()
                .environmentObject(model)
        }
        .defaultSize(width: 370, height: 430)
        .windowResizability(.contentSize)
        .windowStyle(.hiddenTitleBar)

        Settings {
            SettingsView()
                .environmentObject(model)
                .frame(width: 540, height: 390)
        }
    }
}
