import AppKit
import AVFoundation
import Foundation
import UserNotifications

@MainActor
final class NativeAudioService: NSObject, ObservableObject, @preconcurrency AVAudioPlayerDelegate {
    @Published private(set) var isRecording = false
    @Published private(set) var isPlaying = false
    @Published var lastError: String?

    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var recordingURL: URL?

    func startRecording() async -> Bool {
        let allowed = await AVCaptureDevice.requestAccess(for: .audio)
        guard allowed else {
            lastError = "Autorisez le microphone dans Réglages Système."
            return false
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("jarvis-\(UUID().uuidString).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 64_000,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        do {
            let recorder = try AVAudioRecorder(url: url, settings: settings)
            recorder.prepareToRecord()
            guard recorder.record() else { throw CocoaError(.fileWriteUnknown) }
            self.recorder = recorder
            recordingURL = url
            isRecording = true
            lastError = nil
            return true
        } catch {
            lastError = error.localizedDescription
            return false
        }
    }

    func stopRecording() -> Data? {
        recorder?.stop()
        recorder = nil
        isRecording = false
        guard let recordingURL else { return nil }
        defer {
            try? FileManager.default.removeItem(at: recordingURL)
            self.recordingURL = nil
        }
        return try? Data(contentsOf: recordingURL)
    }

    func play(_ data: Data) {
        do {
            player = try AVAudioPlayer(data: data)
            player?.delegate = self
            player?.prepareToPlay()
            isPlaying = player?.play() == true
        } catch {
            lastError = error.localizedDescription
        }
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        isPlaying = false
    }
}

enum NativeNotifications {
    static func requestAuthorization() async {
        _ = try? await UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge])
    }

    static func deliver(_ notification: JarvisNotification) {
        guard notification.priority == "urgent" || notification.priority == "high" else { return }
        let content = UNMutableNotificationContent()
        content.title = notification.title
        content.body = notification.content ?? "Jarvis demande votre attention."
        content.sound = notification.priority == "urgent" ? .defaultCritical : .default
        content.threadIdentifier = "jarvis-\(notification.source)"
        let request = UNNotificationRequest(
            identifier: "jarvis-native-\(notification.id)",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}

@MainActor
enum JarvisCoreLauncher {
    static var projectRoot: String {
        get {
            UserDefaults.standard.string(forKey: "jarvis.projectRoot")
                ?? FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("JARVIS").path
        }
        set { UserDefaults.standard.set(newValue, forKey: "jarvis.projectRoot") }
    }

    static func start() throws {
        let root = URL(fileURLWithPath: projectRoot, isDirectory: true)
        let script = root.appendingPathComponent("scripts/jarvis_full_restart.sh")
        guard FileManager.default.isExecutableFile(atPath: script.path) else {
            throw JarvisAPIError.transport("Script de démarrage introuvable dans \(root.path).")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [script.path, "--daemon", "--no-clean"]
        process.currentDirectoryURL = root
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
    }

    static func revealProject() {
        NSWorkspace.shared.open(URL(fileURLWithPath: projectRoot, isDirectory: true))
    }
}
