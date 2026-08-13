import AppKit
import SwiftUI

struct TerminalView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        // Les deux objets sont observés explicitement : un `ObservableObject`
        // imbriqué ne prévient pas son parent, et la vue resterait figée sur
        // l'état initial du tailnet.
        TerminalContent(bridge: model.terminal, tailscale: model.terminal.tailscale)
            .navigationTitle("Terminal")
    }
}

private struct TerminalContent: View {
    @ObservedObject var bridge: TerminalBridge
    @ObservedObject var tailscale: TailscaleService
    @State private var isConfiguring = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SectionHeader(
                eyebrow: "ACCÈS DISTANT",
                title: "Terminal",
                subtitle: "Une session SSH sur le cœur, par le tailnet. Rien ne transite par Jarvis."
            )
            connectionBar
            surface
            footer
        }
        .padding(28)
        .task { await bridge.refreshPeers() }
    }

    // MARK: - Barre de connexion

    private var connectionBar: some View {
        HStack(spacing: 12) {
            StatusPill(text: tailscale.state.label, color: tailscaleColor)
                .fixedSize()
                .help(tailscale.state.detail)

            machineMenu

            TextField("hôte", text: $bridge.destination.host)
                .textFieldStyle(.plain)
                .frame(minWidth: 150)
                .disabled(bridge.isConnected)

            Text("utilisateur").font(.caption).foregroundStyle(.tertiary)
            TextField("utilisateur", text: $bridge.destination.user)
                .textFieldStyle(.plain)
                .frame(width: 110)
                .disabled(bridge.isConnected)

            Text("port").font(.caption).foregroundStyle(.tertiary)
            TextField("22", value: $bridge.destination.port, format: .number.grouping(.never))
                .textFieldStyle(.plain)
                .frame(width: 46)
                .disabled(bridge.isConnected)

            Spacer(minLength: 8)

            Button {
                isConfiguring.toggle()
            } label: {
                Image(systemName: "slider.horizontal.3")
            }
            .buttonStyle(JarvisSecondaryButtonStyle())
            .help("Clé, touche Option, commande exacte")
            .popover(isPresented: $isConfiguring, arrowEdge: .bottom) { configuration }

            Button(bridge.isConnected ? "Déconnecter" : "Connecter") {
                if bridge.isConnected { bridge.disconnect() } else { bridge.connect() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!bridge.isConnected && !bridge.canConnect)
            .keyboardShortcut(.return, modifiers: [.command, .shift])
        }
        .padding(14)
        .jarvisGlass(cornerRadius: 18)
    }

    private var machineMenu: some View {
        Menu {
            if tailscale.peers.isEmpty {
                Text("Aucune machine détectée")
            }
            ForEach(tailscale.peers) { peer in
                Button {
                    bridge.apply(peer: peer)
                } label: {
                    Label(
                        "\(peer.name) — \(peer.address)",
                        systemImage: peer.isOnline ? "circle.fill" : "circle"
                    )
                }
                .disabled(bridge.isConnected)
            }
            Divider()
            Button("Actualiser le tailnet") {
                Task { await bridge.refreshPeers() }
            }
        } label: {
            Label("Machines", systemImage: "network")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 120)
        .disabled(tailscale.isRefreshing)
    }

    private var configuration: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("PARAMÈTRES DE SESSION")
                .font(.caption2.weight(.semibold))
                .tracking(1.4)
                .foregroundStyle(JarvisPalette.cyan)

            VStack(alignment: .leading, spacing: 6) {
                Text("Clé privée").font(.subheadline.weight(.medium))
                HStack {
                    TextField("~/.ssh/id_ed25519 — vide : configuration ssh habituelle",
                              text: $bridge.destination.identityFile)
                        .textFieldStyle(.roundedBorder)
                    Button("Choisir…") { chooseIdentity() }
                }
                Text("Aucun mot de passe n'est conservé ici. Une passphrase ou une clé d'hôte inconnue est demandée dans le terminal, par ssh lui-même.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Toggle("La touche Option envoie Échap (Meta)", isOn: $bridge.optionSendsMeta)
                .toggleStyle(.switch)

            VStack(alignment: .leading, spacing: 4) {
                Text("Commande exécutée").font(.subheadline.weight(.medium))
                Text(bridge.destination.commandLine)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(18)
        .frame(width: 430)
    }

    // MARK: - Surface

    private var surface: some View {
        ZStack {
            TerminalSurface(bridge: bridge)
            // Toujours présent, seulement transparent : retirer la vue de la
            // pile changerait l'identité structurelle du ZStack et ferait
            // reconstruire la surface AppKit — donc perdre le focus clavier.
            EmptyState(
                symbol: "terminal",
                title: "Aucune session",
                subtitle: "Choisissez une machine du tailnet, puis connectez-vous. La session s'ouvre sur votre compte, avec vos clés SSH."
            )
            .allowsHitTesting(false)
            .opacity(bridge.sessionState == .idle ? 1 : 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(.white.opacity(0.10), lineWidth: 1)
        }
    }

    // MARK: - Pied

    private var footer: some View {
        HStack(spacing: 14) {
            StatusPill(text: sessionLabel, color: sessionColor)
            if !bridge.title.isEmpty {
                Text(bridge.title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Text("⌘C copier · ⌘V coller · ⌘K effacer · ⇧⇞ historique · ⌘± taille")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var tailscaleColor: Color {
        switch tailscale.state {
        case .running: .green
        case .stopped, .unknown: .orange
        case .missing, .failed: .red
        }
    }

    private var sessionLabel: String {
        switch bridge.sessionState {
        case .idle: "Hors ligne"
        case .connecting: "Connexion…"
        case .connected: "Session active — \(bridge.destination.label)"
        case .finished(let reason, _): reason
        }
    }

    private var sessionColor: Color {
        switch bridge.sessionState {
        case .idle: .gray
        case .connecting: .orange
        case .connected: .green
        case .finished(_, let isFailure): isFailure ? .red : .gray
        }
    }

    private func chooseIdentity() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".ssh")
        if panel.runModal() == .OK, let url = panel.url {
            bridge.destination.identityFile = url.path
        }
    }
}
