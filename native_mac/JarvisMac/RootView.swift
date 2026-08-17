import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            JarvisBackdrop()
            switch model.phase {
            case .checking, .offline, .locked, .setupRequired:
                LockView()
            case .ready:
                appShell
            }
        }
        .task {
            await model.bootstrap()
            if ProcessInfo.processInfo.arguments.contains("--export-ui-preview") {
                try? await Task.sleep(for: .seconds(1))
                PreviewExporter.exportToday(model: model)
            }
            if ProcessInfo.processInfo.arguments.contains("--export-connection-state") {
                PreviewExporter.exportConnectionState(model: model)
            }
        }
        .sheet(isPresented: $model.isCommandPalettePresented) {
            CommandPaletteView()
                .environmentObject(model)
        }
        .alert(
            "Jarvis",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )
        ) {
            Button("OK") { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }

    private var appShell: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 250)
        } detail: {
            Group {
                switch model.selectedSection {
                case .today: TodayView()
                case .chat: ChatView()
                case .missions: TasksView(api: model.api)
                case .todos: ActionsView()
                case .memory: MemoryView()
                case .terminal: TerminalView()
                case .system: SystemView()
                }
            }
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    StatusPill(
                        text: model.socket.isConnected ? "Temps réel" : "Reconnexion",
                        color: model.socket.isConnected ? .green : .orange
                    )
                    Button {
                        model.isCommandPalettePresented = true
                    } label: {
                        Label("Demander à Jarvis", systemImage: "command")
                    }
                    .help("Palette Jarvis (⇧⌘J)")
                    if model.selectedSection != .missions {
                        Button {
                            Task { await model.refresh() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .disabled(model.isRefreshing)
                        .help("Actualiser")
                    }
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 11) {
                JarvisOrb(size: 32, active: true)
                VStack(alignment: .leading, spacing: 1) {
                    Text("JARVIS").font(.headline.weight(.bold)).tracking(1.1)
                    Text("PERSONAL INTELLIGENCE").font(.system(size: 8, weight: .medium)).tracking(0.7).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 18)
            .padding(.bottom, 20)

            List(AppSection.allCases, selection: $model.selectedSection) { section in
                HStack(spacing: 10) {
                    Image(systemName: section.symbol)
                        .frame(width: 18)
                        .foregroundStyle(
                            section == .missions ? JarvisPalette.cyan : .primary
                        )
                    VStack(alignment: .leading, spacing: 1) {
                        Text(section.title)
                        if let hint = section.sidebarHint {
                            Text(hint)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .tag(section)
                .padding(.vertical, section.sidebarHint == nil ? 4 : 6)
                .accessibilityElement(children: .combine)
            }
            .listStyle(.sidebar)
            .scrollContentBackground(.hidden)

            Button {
                model.isCommandPalettePresented = true
            } label: {
                HStack {
                    Image(systemName: "sparkle.magnifyingglass")
                    Text("Demander à Jarvis")
                    Spacer()
                    Text("⇧⌘J").font(.caption2).foregroundStyle(.tertiary)
                }
                .padding(11)
            }
            .buttonStyle(.plain)
            .jarvisGlass(cornerRadius: 14)
            .padding(12)
        }
    }
}
