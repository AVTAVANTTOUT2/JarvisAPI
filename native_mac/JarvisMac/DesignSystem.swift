import SwiftUI

enum JarvisPalette {
    static let blue = Color(red: 0.26, green: 0.52, blue: 1.0)
    static let cyan = Color(red: 0.25, green: 0.86, blue: 1.0)
    static let indigo = Color(red: 0.43, green: 0.37, blue: 1.0)
    static let warm = Color(red: 1.0, green: 0.66, blue: 0.25)
}

extension View {
    @ViewBuilder
    func jarvisGlass(cornerRadius: CGFloat = 22) -> some View {
        if #available(macOS 26.0, *) {
            self
                .foregroundStyle(.white)
                .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: cornerRadius))
                .glassEffect(.regular, in: .rect(cornerRadius: cornerRadius))
                .overlay {
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .strokeBorder(.white.opacity(0.10), lineWidth: 1)
                }
        } else {
            self.background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: cornerRadius))
                .overlay {
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .strokeBorder(.white.opacity(0.12), lineWidth: 1)
                }
        }
    }

    func jarvisCardPadding() -> some View { padding(18) }
}

struct JarvisSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(.white.opacity(configuration.isPressed ? 0.18 : 0.09), in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(.white.opacity(0.12), lineWidth: 1)
            }
    }
}

struct JarvisBackdrop: View {
    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor)
            LinearGradient(
                colors: [
                    JarvisPalette.blue.opacity(0.16),
                    .clear,
                    JarvisPalette.indigo.opacity(0.08),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            RadialGradient(
                colors: [JarvisPalette.cyan.opacity(0.10), .clear],
                center: .topTrailing,
                startRadius: 20,
                endRadius: 560
            )
        }
        .ignoresSafeArea()
    }
}

struct JarvisOrb: View {
    var size: CGFloat = 72
    var active = true
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle()
                .fill(JarvisPalette.blue.opacity(active ? 0.20 : 0.08))
                .frame(width: size * 1.22, height: size * 1.22)
                .blur(radius: size * 0.12)
                .scaleEffect(pulse ? 1.08 : 0.92)
            Circle()
                .fill(
                    LinearGradient(
                        colors: active
                            ? [JarvisPalette.cyan, JarvisPalette.blue, JarvisPalette.indigo]
                            : [.gray.opacity(0.7), .gray.opacity(0.35)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: size, height: size)
                .overlay {
                    Circle()
                        .strokeBorder(.white.opacity(0.45), lineWidth: 1)
                        .padding(2)
                }
                .shadow(color: JarvisPalette.blue.opacity(active ? 0.38 : 0), radius: 18)
            Image(systemName: "sparkles")
                .font(.system(size: size * 0.32, weight: .medium))
                .foregroundStyle(.white)
        }
        .onAppear {
            guard active else { return }
            withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
        .accessibilityLabel(active ? "Jarvis opérationnel" : "Jarvis indisponible")
    }
}

struct SectionHeader: View {
    let eyebrow: String
    let title: String
    var subtitle: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(eyebrow.uppercased())
                .font(.caption2.weight(.semibold))
                .tracking(1.4)
                .foregroundStyle(JarvisPalette.cyan)
            Text(title)
                .font(.largeTitle.weight(.semibold))
                .tracking(-0.8)
            if let subtitle {
                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct StatusPill: View {
    let text: String
    let color: Color

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(color.opacity(0.10), in: Capsule())
        .overlay { Capsule().strokeBorder(color.opacity(0.18), lineWidth: 1) }
    }
}

struct EmptyState: View {
    let symbol: String
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 9) {
            Image(systemName: symbol)
                .font(.system(size: 26, weight: .light))
                .foregroundStyle(.tertiary)
            Text(title).font(.headline)
            Text(subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }
}

struct WidgetWindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { configure(view.window) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { configure(nsView.window) }
    }

    private func configure(_ window: NSWindow?) {
        guard let window else { return }
        window.level = .floating
        window.isMovableByWindowBackground = true
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = .clear
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    }
}
