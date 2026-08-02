import AppKit
import SwiftUI

@MainActor
enum PreviewExporter {
    static func exportToday(model: AppModel) {
        let content = ZStack {
            JarvisBackdrop()
            TodayView().environmentObject(model)
        }
        .frame(width: 1100, height: 800)
        .environment(\.colorScheme, .dark)

        let view = NSHostingView(rootView: content)
        view.appearance = NSAppearance(named: .darkAqua)
        view.frame = NSRect(x: 0, y: 0, width: 1100, height: 800)
        view.layoutSubtreeIfNeeded()
        guard let representation = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: representation)
        guard let png = representation.representation(using: .png, properties: [:]) else { return }

        let output = ProcessInfo.processInfo.environment["JARVIS_PREVIEW_OUTPUT"]
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-native-preview.png").path
        try? png.write(to: URL(fileURLWithPath: output), options: .atomic)
    }

    static func exportConnectionState(model: AppModel) {
        let payload: [String: Any] = [
            "phase": model.phase.label,
            "base_url": model.api.baseURLString,
            "ready": model.isReady,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) else { return }
        let output = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-native-connection.json")
        try? data.write(to: output, options: .atomic)
    }
}
