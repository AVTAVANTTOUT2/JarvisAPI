import Darwin
import Foundation

/// Destination SSH. Rien d'autre n'est stocké : ni mot de passe, ni passphrase.
/// L'authentification appartient à `ssh` — clé, agent, ou invite affichée dans
/// le terminal — et jamais à cette application.
struct SSHDestination: Codable, Equatable, Sendable {
    var host: String
    var user: String
    var port: Int
    /// Chemin d'une clé privée. Vide : `ssh` applique sa configuration
    /// habituelle (`~/.ssh/config`, agent, clés par défaut).
    var identityFile: String

    static let empty = SSHDestination(host: "", user: "", port: 22, identityFile: "")

    /// Un hôte ou un utilisateur commençant par `-` serait lu par `ssh` comme
    /// une option : la validation est une frontière d'exécution, pas du confort.
    static func isValidHost(_ value: String) -> Bool {
        guard !value.isEmpty, value.count <= 255, !value.hasPrefix("-") else { return false }
        return value.allSatisfy { $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" || $0 == ":" || $0 == "_" }
    }

    static func isValidUser(_ value: String) -> Bool {
        guard value.count <= 64, !value.hasPrefix("-") else { return false }
        return value.allSatisfy { $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" || $0 == "_" }
    }

    var isValid: Bool {
        SSHDestination.isValidHost(host)
            && SSHDestination.isValidUser(user)
            && (1...65_535).contains(port)
    }

    var label: String {
        let account = user.isEmpty ? host : "\(user)@\(host)"
        return port == 22 ? account : "\(account):\(port)"
    }

    /// Ligne de commande exacte, affichée à l'utilisateur avant connexion.
    /// Aucune option n'affaiblit la vérification de clé d'hôte : une première
    /// connexion demande confirmation dans le terminal, comme ailleurs.
    func arguments() -> [String] {
        var arguments = ["ssh", "-tt"]
        if port != 22 { arguments += ["-p", String(port)] }
        if !user.isEmpty { arguments += ["-l", user] }
        if !identityFile.isEmpty { arguments += ["-i", (identityFile as NSString).expandingTildeInPath] }
        arguments += [
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=20",
            "-o", "ServerAliveCountMax=3",
        ]
        arguments.append(host)
        return arguments
    }

    var commandLine: String { arguments().joined(separator: " ") }
}

enum SSHTerminalState: Equatable, Sendable {
    case idle
    case connecting
    case connected
    /// Fin de session : sortie normale du shell distant, ou échec de `ssh`.
    case finished(reason: String, isFailure: Bool)

    var isRunning: Bool { self == .connecting || self == .connected }
}

/// Session `ssh` lancée sur un pseudo-terminal local.
///
/// `forkpty` plutôt que `Process` : le fils doit être chef de session et
/// posséder son terminal de contrôle, sans quoi `ssh` ne trouve pas `/dev/tty`
/// et ne peut ni demander la confirmation d'une clé d'hôte inconnue, ni lire
/// une passphrase. `Process` n'offre aucun moyen d'appeler `setsid()`.
@MainActor
final class SSHTerminalSession: ObservableObject {
    @Published private(set) var state: SSHTerminalState = .idle {
        didSet { if oldValue != state { onStateChange?(state) } }
    }

    /// Octets reçus du terminal distant.
    var onOutput: ((Data) -> Void)?
    var onStateChange: ((SSHTerminalState) -> Void)?

    private var master: Int32 = -1
    private var child: pid_t = -1
    private var generation = 0
    /// Une fermeture demandée n'est pas une panne : sans ce drapeau, le code de
    /// sortie d'un `SIGHUP` volontaire serait affiché en rouge.
    private var didRequestDisconnect = false

    private static let executable = "/usr/bin/ssh"

    var isRunning: Bool { state.isRunning }

    func start(destination: SSHDestination, columns: Int, rows: Int) {
        guard !isRunning else { return }
        guard destination.isValid else {
            state = .finished(reason: "Destination invalide.", isFailure: true)
            return
        }
        guard FileManager.default.isExecutableFile(atPath: Self.executable) else {
            state = .finished(reason: "\(Self.executable) est introuvable.", isFailure: true)
            return
        }

        // argv et envp sont construits avant le fork : entre `fork` et `execve`,
        // seules les fonctions async-signal-safe sont légitimes.
        var argv = Self.cStrings(destination.arguments())
        var envp = Self.cStrings(Self.childEnvironment())
        defer {
            argv.forEach { free($0) }
            envp.forEach { free($0) }
        }

        var size = winsize(
            ws_row: UInt16(clamping: max(1, rows)),
            ws_col: UInt16(clamping: max(2, columns)),
            ws_xpixel: 0,
            ws_ypixel: 0
        )
        var descriptor: Int32 = -1
        let pid = forkpty(&descriptor, nil, nil, &size)

        if pid < 0 {
            state = .finished(reason: "Impossible d'ouvrir un pseudo-terminal.", isFailure: true)
            return
        }
        if pid == 0 {
            signal(SIGPIPE, SIG_DFL)
            execve(Self.executable, &argv, &envp)
            _exit(127)
        }

        master = descriptor
        child = pid
        generation += 1
        didRequestDisconnect = false
        state = .connecting

        let token = generation
        Self.readLoop(
            master: descriptor,
            child: pid,
            onData: { [weak self] data in
                Task { @MainActor in
                    guard let self, self.generation == token else { return }
                    if self.state == .connecting { self.state = .connected }
                    self.onOutput?(data)
                }
            },
            onExit: { [weak self] status in
                Task { @MainActor in
                    guard let self, self.generation == token else { return }
                    self.finish(status: status)
                }
            }
        )
    }

    /// Envoie des octets au terminal distant.
    func send(_ data: Data) {
        guard isRunning, master >= 0, !data.isEmpty else { return }
        let descriptor = master
        data.withUnsafeBytes { buffer in
            guard var pointer = buffer.baseAddress else { return }
            var remaining = buffer.count
            while remaining > 0 {
                let written = write(descriptor, pointer, remaining)
                if written > 0 {
                    remaining -= written
                    pointer = pointer.advanced(by: written)
                } else if written < 0 && errno == EINTR {
                    continue
                } else {
                    return
                }
            }
        }
    }

    func send(_ text: String) { send(Data(text.utf8)) }

    /// Prévient le programme distant du nouveau format. Sans cela, `vim` et
    /// `htop` continueraient de dessiner sur l'ancienne géométrie.
    func resize(columns: Int, rows: Int) {
        guard isRunning, master >= 0 else { return }
        var size = winsize(
            ws_row: UInt16(clamping: max(1, rows)),
            ws_col: UInt16(clamping: max(2, columns)),
            ws_xpixel: 0,
            ws_ypixel: 0
        )
        _ = ioctl(master, UInt(TIOCSWINSZ), &size)
    }

    /// Ferme la session. `SIGHUP` d'abord : `ssh` prévient l'hôte distant et
    /// rend la main proprement, ce qu'un `SIGKILL` immédiat empêcherait.
    func disconnect() {
        guard child > 0 else { return }
        didRequestDisconnect = true
        kill(child, SIGHUP)
    }

    private func finish(status: Int32) {
        if master >= 0 {
            close(master)
            master = -1
        }
        let pid = child
        child = -1
        if pid > 0 { kill(pid, SIGKILL) }

        let requested = didRequestDisconnect
        didRequestDisconnect = false
        let exitCode = Self.exitCode(from: status)

        if requested {
            state = .finished(reason: "Session fermée.", isFailure: false)
            return
        }
        switch exitCode {
        case 0:
            state = .finished(reason: "Session terminée.", isFailure: false)
        case 255:
            state = .finished(reason: "Connexion SSH interrompue ou refusée.", isFailure: true)
        case 127:
            state = .finished(reason: "ssh n'a pas pu être lancé.", isFailure: true)
        case 129...192:
            state = .finished(reason: "Session interrompue (signal \(exitCode - 128)).", isFailure: true)
        default:
            state = .finished(reason: "Session terminée (code \(exitCode)).", isFailure: true)
        }
    }

    // MARK: - Boucle de lecture

    /// `nonisolated` et statique : le fil de lecture ne capture qu'un
    /// descripteur, un pid et deux fermetures `@Sendable`.
    private nonisolated static func readLoop(
        master: Int32,
        child: pid_t,
        onData: @escaping @Sendable (Data) -> Void,
        onExit: @escaping @Sendable (Int32) -> Void
    ) {
        Thread.detachNewThread {
            var buffer = [UInt8](repeating: 0, count: 64 * 1024)
            while true {
                let count = buffer.withUnsafeMutableBytes { pointer -> Int in
                    guard let base = pointer.baseAddress else { return 0 }
                    return read(master, base, pointer.count)
                }
                if count > 0 {
                    onData(Data(buffer[0..<count]))
                    continue
                }
                if count < 0 && errno == EINTR { continue }
                break // 0 : fin de fichier ; EIO : le fils a fermé son côté
            }
            var status: Int32 = 0
            while waitpid(child, &status, 0) < 0 && errno == EINTR {}
            onExit(status)
        }
    }

    private nonisolated static func exitCode(from status: Int32) -> Int32 {
        // Équivalents Swift des macros `WIFEXITED` / `WEXITSTATUS`, qui ne sont
        // pas importées.
        if status & 0x7F == 0 { return (status >> 8) & 0xFF }
        return 128 + (status & 0x7F)
    }

    // MARK: - Environnement du fils

    /// Environnement minimal et explicite. Tout n'est pas transmis : l'enfant
    /// n'a pas besoin de l'état interne de l'application.
    private nonisolated static func childEnvironment() -> [String] {
        let parent = ProcessInfo.processInfo.environment
        let inherited = [
            "HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR",
            "LANG", "LC_ALL", "LC_CTYPE",
            // Sans ce socket, l'authentification par agent ne fonctionne pas.
            "SSH_AUTH_SOCK",
        ]
        var environment: [String: String] = [:]
        for key in inherited {
            if let value = parent[key], !value.isEmpty { environment[key] = value }
        }
        if environment["PATH"] == nil { environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin" }
        if environment["LANG"] == nil { environment["LANG"] = "fr_FR.UTF-8" }
        environment["TERM"] = "xterm-256color"
        environment["COLORTERM"] = "truecolor"
        environment["TERM_PROGRAM"] = "Jarvis"
        return environment.map { "\($0.key)=\($0.value)" }.sorted()
    }

    private nonisolated static func cStrings(_ values: [String]) -> [UnsafeMutablePointer<CChar>?] {
        var pointers = values.map { strdup($0) }
        pointers.append(nil)
        return pointers
    }
}
