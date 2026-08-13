import AppKit

/// Palette du terminal. Froide et sombre, accordée à `JarvisPalette`, mais
/// définie en `NSColor` : le rendu passe par Core Text, pas par SwiftUI.
enum TerminalTheme {
    static let background = NSColor(srgbRed: 0.02, green: 0.03, blue: 0.06, alpha: 0.55)
    static let foreground = NSColor(srgbRed: 0.88, green: 0.91, blue: 0.96, alpha: 1.0)
    static let cursor = NSColor(srgbRed: 0.25, green: 0.86, blue: 1.0, alpha: 0.92)
    static let cursorText = NSColor(srgbRed: 0.02, green: 0.05, blue: 0.09, alpha: 1.0)
    static let selection = NSColor(srgbRed: 0.26, green: 0.52, blue: 1.0, alpha: 0.32)

    /// Seize couleurs ANSI. Les vives ne sont pas de simples éclaircissements :
    /// elles gardent la même température pour éviter le rendu criard des
    /// palettes VGA d'origine.
    private static let ansi: [NSColor] = [
        NSColor(srgbRed: 0.16, green: 0.18, blue: 0.23, alpha: 1),  // noir
        NSColor(srgbRed: 0.96, green: 0.38, blue: 0.42, alpha: 1),  // rouge
        NSColor(srgbRed: 0.40, green: 0.86, blue: 0.58, alpha: 1),  // vert
        NSColor(srgbRed: 0.96, green: 0.76, blue: 0.38, alpha: 1),  // jaune
        NSColor(srgbRed: 0.36, green: 0.62, blue: 1.00, alpha: 1),  // bleu
        NSColor(srgbRed: 0.76, green: 0.55, blue: 1.00, alpha: 1),  // magenta
        NSColor(srgbRed: 0.25, green: 0.86, blue: 1.00, alpha: 1),  // cyan
        NSColor(srgbRed: 0.82, green: 0.86, blue: 0.92, alpha: 1),  // blanc
        NSColor(srgbRed: 0.35, green: 0.39, blue: 0.46, alpha: 1),
        NSColor(srgbRed: 1.00, green: 0.52, blue: 0.55, alpha: 1),
        NSColor(srgbRed: 0.55, green: 0.94, blue: 0.70, alpha: 1),
        NSColor(srgbRed: 1.00, green: 0.85, blue: 0.52, alpha: 1),
        NSColor(srgbRed: 0.52, green: 0.74, blue: 1.00, alpha: 1),
        NSColor(srgbRed: 0.86, green: 0.68, blue: 1.00, alpha: 1),
        NSColor(srgbRed: 0.50, green: 0.93, blue: 1.00, alpha: 1),
        NSColor(srgbRed: 1.00, green: 1.00, blue: 1.00, alpha: 1),
    ]

    private static let extended: [NSColor] = {
        var colors = ansi
        let levels: [CGFloat] = [0, 95, 135, 175, 215, 255].map { $0 / 255 }
        for red in levels {
            for green in levels {
                for blue in levels {
                    colors.append(NSColor(srgbRed: red, green: green, blue: blue, alpha: 1))
                }
            }
        }
        for step in 0..<24 {
            let level = CGFloat(8 + step * 10) / 255
            colors.append(NSColor(srgbRed: level, green: level, blue: level, alpha: 1))
        }
        return colors
    }()

    static func color(_ color: TerminalColor, fallback: NSColor) -> NSColor {
        switch color {
        case .default:
            return fallback
        case .indexed(let index):
            let position = Int(index)
            return position < extended.count ? extended[position] : fallback
        case .rgb(let red, let green, let blue):
            return NSColor(
                srgbRed: CGFloat(red) / 255,
                green: CGFloat(green) / 255,
                blue: CGFloat(blue) / 255,
                alpha: 1
            )
        }
    }

    /// Couleurs effectives d'une cellule, vidéo inverse et atténuation
    /// appliquées. `nil` en fond signifie « ne rien peindre » : le verre de
    /// l'application reste visible.
    static func resolve(_ attributes: TerminalAttributes) -> (foreground: NSColor, background: NSColor?) {
        var front = color(attributes.foreground, fallback: foreground)
        var back: NSColor? = {
            if case .default = attributes.background { return nil }
            return color(attributes.background, fallback: background)
        }()

        if attributes.bold, case .indexed(let index) = attributes.foreground, index < 8 {
            front = color(.indexed(index + 8), fallback: front)
        }
        if attributes.inverse {
            let previousFront = front
            front = back ?? background.withAlphaComponent(1.0)
            back = previousFront
        }
        if attributes.dim {
            front = front.withAlphaComponent(0.55)
        }
        if attributes.hidden {
            front = front.withAlphaComponent(0.0)
        }
        return (front, back)
    }
}
