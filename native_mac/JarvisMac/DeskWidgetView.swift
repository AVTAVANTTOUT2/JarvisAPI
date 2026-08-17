import SwiftUI

struct DeskWidgetView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                JarvisOrb(size: 38, active: model.isReady)
                VStack(alignment: .leading, spacing: 1) {
                    Text("JARVIS GLANCE").font(.caption.weight(.bold)).tracking(1.1)
                    Text(Date.now.formatted(.dateTime.weekday(.wide).day().month(.wide)).capitalized)
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Button { dismissWindow(id: "glance") } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.tertiary)
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(model.snapshot.tasks.first?.title ?? "Tout est sous contrôle.")
                    .font(.title3.weight(.semibold)).lineLimit(3)
                if let task = model.snapshot.tasks.first {
                    Text("Prochain élément à faire · \(task.priorityLabel)")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Rien d’urgent dans À faire").font(.caption).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 75, alignment: .leading)

            HStack(spacing: 10) {
                glanceMetric("\(model.snapshot.tasks.count)", "à faire", "checkmark.circle")
                glanceMetric("\(model.snapshot.notifications.count)", "signaux", "bell")
                glanceMetric("\(model.snapshot.calendar.count)", "agenda", "calendar")
            }

            if let notification = model.snapshot.notifications.first {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "bell.badge.fill").foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(notification.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                        Text(notification.content ?? "Jarvis demande votre attention.")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                .padding(11)
                .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 13))
            }

            HStack {
                Button("Parler") {
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    model.selectedSection = .chat
                }
                .buttonStyle(.borderedProminent)
                Button("Actualiser") { Task { await model.refresh() } }
                    .buttonStyle(JarvisSecondaryButtonStyle())
                Spacer()
                StatusPill(text: model.phase.label, color: model.phase.color)
            }
        }
        .padding(20)
        .background(WidgetWindowConfigurator())
        .jarvisGlass(cornerRadius: 26)
        .padding(7)
    }

    private func glanceMetric(_ value: String, _ label: String, _ symbol: String) -> some View {
        VStack(spacing: 5) {
            Image(systemName: symbol).foregroundStyle(JarvisPalette.cyan)
            Text(value).font(.headline)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 9)
        .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
    }
}
