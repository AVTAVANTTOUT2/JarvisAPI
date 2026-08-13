import Foundation

struct TailscalePeer: Identifiable, Equatable, Sendable {
    let id: String
    /// Nom court affiché, par exemple `mac-mini-de-zeldris`.
    let name: String
    /// Nom MagicDNS complet, sans point final.
    let dnsName: String
    let address: String
    let os: String
    let isOnline: Bool

    /// Ce que l'on passe à `ssh` : le nom MagicDNS quand il existe, sinon
    /// l'adresse `100.x`. L'adresse reste jointe si le DNS du tailnet est coupé.
    var sshHost: String { dnsName.isEmpty ? address : dnsName }
}

enum TailscaleState: Equatable, Sendable {
    case unknown
    /// Aucun binaire `tailscale` trouvé sur la machine.
    case missing
    /// Binaire présent, réseau arrêté ou déconnecté.
    case stopped(String)
    case running(selfName: String)
    case failed(String)

    var isRunning: Bool {
        if case .running = self { return true }
        return false
    }

    /// Court : la pastille tient sur une ligne. Le détail va dans l'infobulle.
    var label: String {
        switch self {
        case .unknown: "Tailnet…"
        case .missing: "Tailscale absent"
        case .stopped: "Tailnet inactif"
        case .running: "Tailnet actif"
        case .failed: "Tailnet injoignable"
        }
    }

    var detail: String {
        switch self {
        case .unknown:
            "État du tailnet en cours de lecture."
        case .missing:
            "Aucun binaire tailscale trouvé. La connexion par nom de machine ne sera pas proposée ; une adresse reste saisissable à la main."
        case .stopped(let backend):
            switch backend {
            case "Stopped": "Tailscale est installé mais arrêté."
            case "NeedsLogin": "Tailscale attend une connexion à votre compte."
            case "Starting": "Tailscale démarre."
            default: "Tailscale signale l'état « \(backend) »."
            }
        case .running(let name):
            "Tailscale actif — cette machine est \(name) dans le tailnet."
        case .failed(let reason):
            "Le démon Tailscale local n'a pas répondu (\(reason))."
        }
    }
}

/// Découverte des machines du tailnet par la ligne de commande Tailscale.
///
/// Aucune API réseau n'est appelée : `tailscale status --json` interroge le
/// démon local. Le service ne sert qu'à *proposer* des hôtes — la connexion
/// reste faite par `ssh`, avec ses propres clés.
@MainActor
final class TailscaleService: ObservableObject {
    @Published private(set) var state: TailscaleState = .unknown
    @Published private(set) var peers: [TailscalePeer] = []
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastRefresh: Date?

    private static let candidates = [
        "/usr/local/bin/tailscale",
        "/opt/homebrew/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/usr/bin/tailscale",
    ]

    static var executablePath: String? {
        candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer {
            isRefreshing = false
            lastRefresh = .now
        }

        guard let executable = Self.executablePath else {
            state = .missing
            peers = []
            return
        }

        let output = await Task.detached {
            Self.run(executable: executable, arguments: ["status", "--json"], timeout: 6)
        }.value

        guard let output, let payload = try? JSONDecoder().decode(StatusPayload.self, from: output) else {
            state = .failed("Réponse illisible")
            return
        }

        let backend = payload.backendState ?? "Unknown"
        let selfName = payload.selfNode.map { Self.shortName($0, suffix: payload.magicDNSSuffix) } ?? "cette machine"
        state = backend == "Running" ? .running(selfName: selfName) : .stopped(backend)

        let selfID = payload.selfNode?.id
        peers = (payload.peer ?? [:])
            .compactMap { key, node -> TailscalePeer? in
                guard node.id != selfID else { return nil }
                let address = node.tailscaleIPs?.first { !$0.contains(":") } ?? node.tailscaleIPs?.first ?? ""
                let dnsName = (node.dnsName ?? "").hasSuffix(".")
                    ? String((node.dnsName ?? "").dropLast())
                    : (node.dnsName ?? "")
                guard !address.isEmpty || !dnsName.isEmpty else { return nil }
                return TailscalePeer(
                    id: node.id ?? key,
                    name: Self.shortName(node, suffix: payload.magicDNSSuffix),
                    dnsName: dnsName,
                    address: address,
                    os: node.os ?? "",
                    isOnline: node.online ?? false
                )
            }
            // En ligne d'abord : la machine qu'on veut atteindre est presque
            // toujours celle qui répond.
            .sorted { ($0.isOnline ? 0 : 1, $0.name) < ($1.isOnline ? 0 : 1, $1.name) }
    }

    /// Le nom MagicDNS d'abord : c'est celui que le tailnet garantit unique et
    /// que l'on tape réellement. `HostName` est un nom d'affichage, parfois
    /// générique — un iPhone s'y annonce « localhost ».
    private static func shortName(_ node: StatusPayload.Node, suffix: String?) -> String {
        if var dns = node.dnsName, !dns.isEmpty {
            if dns.hasSuffix(".") { dns.removeLast() }
            if let suffix, dns.hasSuffix("." + suffix) { dns.removeLast(suffix.count + 1) }
            if !dns.isEmpty { return dns }
        }
        if let hostName = node.hostName, !hostName.isEmpty { return hostName }
        return "machine inconnue"
    }

    private final class OutputBox: @unchecked Sendable {
        var data = Data()
    }

    /// Exécution bornée dans le temps. La lecture est déportée sur un autre
    /// fil : `readToEnd()` ne rend la main qu'à la fermeture du tube, donc
    /// attendre dessus reviendrait à n'avoir aucune borne.
    private nonisolated static func run(executable: String, arguments: [String], timeout: TimeInterval) -> Data? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice

        do { try process.run() } catch { return nil }

        let box = OutputBox()
        let semaphore = DispatchSemaphore(value: 0)
        DispatchQueue.global(qos: .userInitiated).async {
            box.data = (try? pipe.fileHandleForReading.readToEnd()) ?? Data()
            semaphore.signal()
        }

        if semaphore.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            // La fin du processus ferme le tube, ce qui débloque la lecture.
            _ = semaphore.wait(timeout: .now() + 2)
            return nil
        }
        process.waitUntilExit()
        return process.terminationStatus == 0 ? box.data : nil
    }

    private struct StatusPayload: Decodable {
        struct Node: Decodable {
            let id: String?
            let hostName: String?
            let dnsName: String?
            let tailscaleIPs: [String]?
            let os: String?
            let online: Bool?

            enum CodingKeys: String, CodingKey {
                case id = "ID"
                case hostName = "HostName"
                case dnsName = "DNSName"
                case tailscaleIPs = "TailscaleIPs"
                case os = "OS"
                case online = "Online"
            }
        }

        let backendState: String?
        let magicDNSSuffix: String?
        let selfNode: Node?
        let peer: [String: Node]?

        enum CodingKeys: String, CodingKey {
            case backendState = "BackendState"
            case magicDNSSuffix = "MagicDNSSuffix"
            case selfNode = "Self"
            case peer = "Peer"
        }
    }
}
