import Foundation

/// Pont terminal : réunit la découverte Tailscale, la session `ssh` et
/// l'émulateur, et survit aux changements de section — une connexion ne doit
/// pas tomber parce qu'on est passé voir l'agenda.
@MainActor
final class TerminalBridge: ObservableObject {
    @Published var destination: SSHDestination { didSet { persistDestination() } }
    /// Option agit comme Meta (Emacs, tmux). Désactivé par défaut : sur un
    /// clavier français, Option sert d'abord à taper `#`, `{`, `[`.
    @Published var optionSendsMeta: Bool {
        didSet { UserDefaults.standard.set(optionSendsMeta, forKey: Keys.optionSendsMeta) }
    }
    @Published private(set) var sessionState: SSHTerminalState = .idle
    @Published private(set) var title = ""

    let emulator = TerminalEmulator()
    let session = SSHTerminalSession()
    let tailscale = TailscaleService()

    private enum Keys {
        static let host = "jarvis.terminal.host"
        static let user = "jarvis.terminal.user"
        static let port = "jarvis.terminal.port"
        static let identity = "jarvis.terminal.identity"
        static let optionSendsMeta = "jarvis.terminal.optionSendsMeta"
    }

    init() {
        let defaults = UserDefaults.standard
        destination = SSHDestination(
            host: defaults.string(forKey: Keys.host) ?? "",
            user: defaults.string(forKey: Keys.user) ?? NSUserName(),
            port: defaults.object(forKey: Keys.port) as? Int ?? 22,
            identityFile: defaults.string(forKey: Keys.identity) ?? ""
        )
        optionSendsMeta = defaults.bool(forKey: Keys.optionSendsMeta)

        session.onOutput = { [weak self] data in
            self?.emulator.feed(data)
        }
        session.onStateChange = { [weak self] state in
            self?.handle(state)
        }
        emulator.onResponse = { [weak self] data in
            self?.session.send(data)
        }
        emulator.onTitleChange = { [weak self] value in
            self?.title = value
        }
    }

    /// Dérivé de l'état republié, et non lu dans la session : c'est celui-là
    /// que SwiftUI observe, donc le seul qui garantisse un rendu cohérent.
    var isConnected: Bool { sessionState.isRunning }
    var canConnect: Bool { destination.isValid && !sessionState.isRunning }

    // MARK: - Session

    func connect() {
        guard canConnect else { return }
        writeNotice("Connexion à \(destination.label)")
        writeNotice(destination.commandLine)
        session.start(
            destination: destination,
            columns: emulator.columns,
            rows: emulator.rows
        )
    }

    func disconnect() {
        guard session.isRunning else { return }
        session.disconnect()
    }

    /// Appelé à la fermeture de session applicative : laisser un shell distant
    /// ouvert derrière l'écran verrouillé annulerait le verrou.
    func teardown() {
        session.disconnect()
        emulator.clearAll()
        title = ""
    }

    func send(_ data: Data) { session.send(data) }

    func clear() {
        emulator.clearAll()
        // `Ctrl-L` laisse le shell distant redessiner son invite ; sans lui,
        // l'écran resterait vide jusqu'à la prochaine frappe.
        if session.isRunning { session.send(Data([0x0C])) }
    }

    func resize(columns: Int, rows: Int) {
        guard columns != emulator.columns || rows != emulator.rows else { return }
        emulator.resize(columns: columns, rows: rows)
        session.resize(columns: columns, rows: rows)
    }

    func apply(peer: TailscalePeer) {
        destination.host = peer.sshHost
    }

    func refreshPeers() async { await tailscale.refresh() }

    // MARK: - Détails

    private func handle(_ state: SSHTerminalState) {
        sessionState = state
        if case .finished(let reason, _) = state {
            writeNotice(reason)
            title = ""
        }
    }

    /// Message de l'application dans le flux du terminal, en gris atténué pour
    /// qu'on ne le confonde jamais avec une sortie de la machine distante.
    private func writeNotice(_ text: String) {
        let sanitized = text.filter { !$0.isNewline && $0 != "\u{1B}" }
        emulator.feed(Data("\r\n\u{1B}[2m— \(sanitized)\u{1B}[0m\r\n".utf8))
    }

    private func persistDestination() {
        let defaults = UserDefaults.standard
        defaults.set(destination.host, forKey: Keys.host)
        defaults.set(destination.user, forKey: Keys.user)
        defaults.set(destination.port, forKey: Keys.port)
        defaults.set(destination.identityFile, forKey: Keys.identity)
    }
}
