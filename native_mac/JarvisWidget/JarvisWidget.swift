import SwiftUI
import WidgetKit

struct JarvisWidgetEntry: TimelineEntry {
    let date: Date
    let coreOnline: Bool
    let configured: Bool
}

struct JarvisWidgetProvider: TimelineProvider {
    func placeholder(in context: Context) -> JarvisWidgetEntry {
        JarvisWidgetEntry(date: .now, coreOnline: true, configured: true)
    }

    func getSnapshot(in context: Context, completion: @escaping (JarvisWidgetEntry) -> Void) {
        completion(JarvisWidgetEntry(date: .now, coreOnline: true, configured: true))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<JarvisWidgetEntry>) -> Void) {
        let completionBox = TimelineCompletionBox(completion)
        guard let url = URL(string: "https://127.0.0.1:8081/api/auth/status") else {
            completionBox.call(timeline(online: false, configured: false))
            return
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3
        let trustDelegate = WidgetTrustDelegate()
        let session = URLSession(configuration: .default, delegate: trustDelegate, delegateQueue: nil)
        session.dataTask(with: request) { data, response, _ in
            let online = (response as? HTTPURLResponse).map { 200..<500 ~= $0.statusCode } ?? false
            let configured: Bool
            if
                let data,
                let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            {
                configured = object["configured"] as? Bool ?? false
            } else {
                configured = false
            }
            completionBox.call(timeline(online: online, configured: configured))
        }.resume()
    }

    private func timeline(online: Bool, configured: Bool) -> Timeline<JarvisWidgetEntry> {
        Timeline(
            entries: [JarvisWidgetEntry(date: .now, coreOnline: online, configured: configured)],
            policy: .after(Date.now.addingTimeInterval(5 * 60))
        )
    }
}

private final class TimelineCompletionBox: @unchecked Sendable {
    private let callback: (Timeline<JarvisWidgetEntry>) -> Void

    init(_ callback: @escaping (Timeline<JarvisWidgetEntry>) -> Void) {
        self.callback = callback
    }

    func call(_ timeline: Timeline<JarvisWidgetEntry>) { callback(timeline) }
}

private final class WidgetTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        let host = challenge.protectionSpace.host.lowercased()
        if
            ["localhost", "127.0.0.1", "::1"].contains(host),
            challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust
        {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}

struct JarvisWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: JarvisWidgetEntry

    var body: some View {
        Link(destination: URL(string: "jarvis://today")!) {
            VStack(alignment: .leading, spacing: family == .systemSmall ? 9 : 12) {
                HStack {
                    ZStack {
                        Circle().fill(
                            LinearGradient(colors: [.cyan, .blue, .indigo], startPoint: .topLeading, endPoint: .bottomTrailing)
                        )
                        Image(systemName: "sparkles").foregroundStyle(.white).font(.caption.weight(.bold))
                    }
                    .frame(width: 29, height: 29)
                    Spacer()
                    Circle().fill(entry.coreOnline ? .green : .orange).frame(width: 8, height: 8)
                }
                Text(greeting)
                    .font(family == .systemSmall ? .headline : .title3.weight(.semibold))
                    .lineLimit(2)
                if family != .systemSmall {
                    Text(entry.coreOnline ? "Le cœur local est prêt. Ouvrez Jarvis pour votre briefing et vos actions." : "Le cœur est hors ligne. Ouvrez Jarvis pour le réveiller.")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
                Spacer(minLength: 0)
                Label(entry.coreOnline ? "Jarvis disponible" : "Connexion requise", systemImage: entry.coreOnline ? "bolt.fill" : "bolt.slash")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(entry.coreOnline ? .cyan : .orange)
            }
            .containerBackground(for: .widget) {
                LinearGradient(
                    colors: [Color.blue.opacity(0.17), Color.black.opacity(0.06)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            }
        }
        .widgetURL(URL(string: "jarvis://today"))
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: entry.date)
        if hour < 12 { return "Bonjour. Prêt pour la journée ?" }
        if hour < 18 { return "Votre journée, en un regard." }
        return "Terminons la journée proprement."
    }
}

struct JarvisGlanceWidget: Widget {
    let kind = "JarvisGlanceWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: JarvisWidgetProvider()) { entry in
            JarvisWidgetView(entry: entry)
        }
        .configurationDisplayName("Jarvis Glance")
        .description("L’état du cœur Jarvis et un accès immédiat à votre briefing.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct JarvisWidgetBundle: WidgetBundle {
    var body: some Widget { JarvisGlanceWidget() }
}
