import AppKit
import SwiftUI

struct TerminalPosition: Equatable, Comparable {
    var line: Int
    var column: Int

    static func < (lhs: TerminalPosition, rhs: TerminalPosition) -> Bool {
        lhs.line == rhs.line ? lhs.column < rhs.column : lhs.line < rhs.line
    }
}

/// Surface du terminal. AppKit plutôt que SwiftUI : il faut un premier
/// répondeur qui reçoive les touches brutes, la composition des touches
/// mortes, la molette et la sélection — et un dessin par lignes que `Canvas`
/// ne rendrait pas plus simple.
struct TerminalSurface: NSViewRepresentable {
    @ObservedObject var bridge: TerminalBridge

    func makeNSView(context: Context) -> TerminalNSView {
        let view = TerminalNSView(emulator: bridge.emulator)
        view.onInput = { [weak bridge] data in bridge?.send(data) }
        view.onGeometryChange = { [weak bridge] columns, rows in
            bridge?.resize(columns: columns, rows: rows)
        }
        view.onClear = { [weak bridge] in bridge?.clear() }
        view.optionSendsMeta = bridge.optionSendsMeta
        return view
    }

    func updateNSView(_ nsView: TerminalNSView, context: Context) {
        nsView.optionSendsMeta = bridge.optionSendsMeta
    }
}

final class TerminalNSView: NSView, @preconcurrency NSTextInputClient {
    var onInput: ((Data) -> Void)?
    var onGeometryChange: ((Int, Int) -> Void)?
    var onClear: (() -> Void)?
    var optionSendsMeta = false

    private let emulator: TerminalEmulator
    private let padding: CGFloat = 10

    private var fontSize: CGFloat
    private var regularFont: NSFont
    private var boldFont: NSFont
    private var italicFont: NSFont
    private var boldItalicFont: NSFont
    private var cellSize = CGSize(width: 8, height: 16)
    private var ascent: CGFloat = 12

    private var scrollOffset = 0
    private var scrollAccumulator: CGFloat = 0
    private var lastTotalLines = 0

    private var selectionAnchor: TerminalPosition?
    private var selectionHead: TerminalPosition?
    private var markedText = ""

    private static let fontSizeKey = "jarvis.terminal.fontSize"

    init(emulator: TerminalEmulator) {
        self.emulator = emulator
        let stored = UserDefaults.standard.double(forKey: Self.fontSizeKey)
        fontSize = stored >= 9 && stored <= 24 ? stored : 12.5
        regularFont = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular)
        boldFont = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .bold)
        italicFont = regularFont
        boldItalicFont = boldFont
        super.init(frame: .zero)
        updateFonts()
        emulator.addScreenObserver(owner: self) { [weak self] in self?.screenDidChange() }
        lastTotalLines = emulator.totalLines
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) n'est pas utilisé") }

    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window, window.firstResponder !== self else { return }
        window.makeFirstResponder(self)
    }

    override func becomeFirstResponder() -> Bool {
        needsDisplay = true
        return super.becomeFirstResponder()
    }

    override func resignFirstResponder() -> Bool {
        needsDisplay = true
        return super.resignFirstResponder()
    }

    // MARK: - Géométrie

    private func updateFonts() {
        regularFont = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular)
        boldFont = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .bold)
        italicFont = NSFontManager.shared.convert(regularFont, toHaveTrait: .italicFontMask)
        boldItalicFont = NSFontManager.shared.convert(boldFont, toHaveTrait: .italicFontMask)

        let advance = ("0" as NSString).size(withAttributes: [.font: regularFont]).width
        ascent = ceil(regularFont.ascender)
        let descent = ceil(-regularFont.descender)
        cellSize = CGSize(width: max(4, advance), height: max(8, ascent + descent + 2))
        needsDisplay = true
    }

    private func adjustFontSize(_ delta: CGFloat) {
        let updated = min(24, max(9, fontSize + delta))
        guard updated != fontSize else { return }
        fontSize = updated
        UserDefaults.standard.set(Double(updated), forKey: Self.fontSizeKey)
        updateFonts()
        updateGeometry()
    }

    override func layout() {
        super.layout()
        updateGeometry()
    }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        updateGeometry()
    }

    private func updateGeometry() {
        guard cellSize.width > 0, cellSize.height > 0, bounds.width > 0, bounds.height > 0 else { return }
        let columns = max(20, Int(floor((bounds.width - padding * 2) / cellSize.width)))
        let rows = max(4, Int(floor((bounds.height - padding * 2) / cellSize.height)))
        guard columns != emulator.columns || rows != emulator.rows else { return }
        onGeometryChange?(columns, rows)
    }

    private func screenDidChange() {
        let total = emulator.totalLines
        if scrollOffset > 0 {
            // La vue reste sur ce que l'utilisateur lit : l'historique grandit
            // sous lui, on décale d'autant.
            scrollOffset = min(scrollOffset + max(0, total - lastTotalLines), maximumScrollOffset)
        }
        lastTotalLines = total
        needsDisplay = true
    }

    private var maximumScrollOffset: Int {
        emulator.isAlternateScreen ? 0 : max(0, emulator.totalLines - emulator.rows)
    }

    private var firstVisibleLine: Int {
        max(0, emulator.totalLines - emulator.rows - scrollOffset)
    }

    private func setScrollOffset(_ value: Int) {
        let clamped = min(max(0, value), maximumScrollOffset)
        guard clamped != scrollOffset else { return }
        scrollOffset = clamped
        needsDisplay = true
    }

    private func scrollToBottom() { setScrollOffset(0) }

    // MARK: - Dessin

    override func draw(_ dirtyRect: NSRect) {
        TerminalTheme.background.setFill()
        bounds.fill()

        let selection = normalizedSelection()
        let first = firstVisibleLine
        for visualRow in 0..<emulator.rows {
            guard let cells = emulator.line(at: first + visualRow) else { continue }
            draw(
                line: cells,
                lineIndex: first + visualRow,
                visualRow: visualRow,
                selection: selection
            )
        }
        drawCursor(first: first)
    }

    private func draw(
        line cells: [TerminalCell],
        lineIndex: Int,
        visualRow: Int,
        selection: (start: TerminalPosition, end: TerminalPosition)?
    ) {
        let top = padding + CGFloat(visualRow) * cellSize.height
        let underlineY = top + ascent + 2

        var index = 0
        while index < cells.count {
            let attributes = cells[index].attributes
            let isWide = cells[index].isWide
            var end = index + 1
            // Un glyphe double largeur est positionné seul : on ne peut pas
            // supposer que sa chasse vaut exactement deux cellules.
            if !isWide {
                while end < cells.count,
                      cells[end].attributes == attributes,
                      !cells[end].isWide,
                      !cells[end].isContinuation {
                    end += 1
                }
            } else if end < cells.count, cells[end].isContinuation {
                end += 1
            }

            let rect = CGRect(
                x: padding + CGFloat(index) * cellSize.width,
                y: top,
                width: CGFloat(end - index) * cellSize.width,
                height: cellSize.height
            )
            let resolved = TerminalTheme.resolve(attributes)
            if let background = resolved.background {
                background.setFill()
                rect.fill()
            }

            var text = ""
            for cell in cells[index..<end] where !cell.isContinuation {
                text.append(cell.character)
            }
            if !text.trimmingCharacters(in: .whitespaces).isEmpty {
                drawText(
                    text,
                    attributes: attributes,
                    color: resolved.foreground,
                    x: rect.minX,
                    top: top
                )
            }
            if attributes.underline {
                resolved.foreground.setFill()
                CGRect(x: rect.minX, y: underlineY, width: rect.width, height: 1).fill()
            }
            if attributes.strikethrough {
                resolved.foreground.setFill()
                CGRect(x: rect.minX, y: top + cellSize.height * 0.5, width: rect.width, height: 1).fill()
            }
            index = end
        }

        if let selection, let range = selectedColumns(on: lineIndex, selection: selection, width: cells.count) {
            TerminalTheme.selection.setFill()
            CGRect(
                x: padding + CGFloat(range.lowerBound) * cellSize.width,
                y: top,
                width: CGFloat(range.count) * cellSize.width,
                height: cellSize.height
            ).fill()
        }
    }

    /// Le tracé passe par AppKit et non par `CTLineDraw` : dans une fenêtre
    /// réelle, la vue est adossée à un calque et le retournement du repère
    /// n'est pas porté par la CTM du contexte. Une matrice de texte inversée
    /// à la main y dessinait hors champ — invisible à l'écran, correct dans un
    /// rendu bitmap hors écran, donc indétectable sans lancer l'application.
    /// `NSAttributedString.draw(at:)` interroge le contexte courant lui-même.
    private func drawText(
        _ text: String,
        attributes: TerminalAttributes,
        color: NSColor,
        x: CGFloat,
        top: CGFloat
    ) {
        let font: NSFont
        switch (attributes.bold, attributes.italic) {
        case (true, true): font = boldItalicFont
        case (true, false): font = boldFont
        case (false, true): font = italicFont
        case (false, false): font = regularFont
        }
        NSAttributedString(
            string: text,
            attributes: [
                .font: font,
                .foregroundColor: color,
                // Une ligature fusionnerait deux glyphes et décalerait la grille.
                .ligature: 0,
            ]
        )
        .draw(at: NSPoint(x: x, y: top))
    }

    private func drawCursor(first: Int) {
        guard emulator.cursor.visible, scrollOffset == 0 else { return }
        let lineIndex = emulator.firstScreenLine + emulator.cursor.row
        let visualRow = lineIndex - first
        guard visualRow >= 0, visualRow < emulator.rows else { return }

        let rect = CGRect(
            x: padding + CGFloat(emulator.cursor.column) * cellSize.width,
            y: padding + CGFloat(visualRow) * cellSize.height,
            width: cellSize.width,
            height: cellSize.height
        )
        let focused = window?.firstResponder === self && window?.isKeyWindow == true
        guard focused else {
            TerminalTheme.cursor.withAlphaComponent(0.55).setStroke()
            let path = NSBezierPath(rect: rect.insetBy(dx: 0.5, dy: 0.5))
            path.lineWidth = 1
            path.stroke()
            return
        }
        TerminalTheme.cursor.setFill()
        rect.fill()

        guard let cells = emulator.line(at: lineIndex),
              emulator.cursor.column < cells.count else { return }
        let cell = cells[emulator.cursor.column]
        guard !cell.isContinuation, cell.character != " " else { return }
        drawText(
            String(cell.character),
            attributes: cell.attributes,
            color: TerminalTheme.cursorText,
            x: rect.minX,
            top: rect.minY
        )
    }

    // MARK: - Sélection

    private func normalizedSelection() -> (start: TerminalPosition, end: TerminalPosition)? {
        guard let anchor = selectionAnchor, let head = selectionHead, anchor != head else { return nil }
        return anchor < head ? (anchor, head) : (head, anchor)
    }

    private func selectedColumns(
        on line: Int,
        selection: (start: TerminalPosition, end: TerminalPosition),
        width: Int
    ) -> Range<Int>? {
        guard line >= selection.start.line, line <= selection.end.line, width > 0 else { return nil }
        let lower = line == selection.start.line ? min(selection.start.column, width) : 0
        let upper = line == selection.end.line ? min(selection.end.column, width) : width
        guard lower < upper else { return nil }
        return lower..<upper
    }

    private func position(at point: NSPoint) -> TerminalPosition {
        let column = Int(floor((point.x - padding) / cellSize.width))
        let row = Int(floor((point.y - padding) / cellSize.height))
        return TerminalPosition(
            line: firstVisibleLine + min(max(row, 0), emulator.rows - 1),
            column: min(max(column, 0), emulator.columns)
        )
    }

    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        let point = convert(event.locationInWindow, from: nil)
        selectionAnchor = position(at: point)
        selectionHead = selectionAnchor
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        selectionHead = position(at: point)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        if normalizedSelection() == nil {
            selectionAnchor = nil
            selectionHead = nil
            needsDisplay = true
        }
    }

    override func scrollWheel(with event: NSEvent) {
        guard cellSize.height > 0 else { return }
        let delta = event.hasPreciseScrollingDeltas
            ? event.scrollingDeltaY
            : event.scrollingDeltaY * cellSize.height
        scrollAccumulator += delta
        let steps = Int(scrollAccumulator / cellSize.height)
        guard steps != 0 else { return }
        scrollAccumulator -= CGFloat(steps) * cellSize.height
        setScrollOffset(scrollOffset + steps)
    }

    // MARK: - Copier / coller

    var hasSelection: Bool { normalizedSelection() != nil }

    @objc func copySelection(_ sender: Any?) {
        guard let selection = normalizedSelection() else { return }
        let text = emulator.text(
            from: (line: selection.start.line, column: selection.start.column),
            to: (line: selection.end.line, column: selection.end.column)
        )
        guard !text.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    @objc func pasteFromPasteboard(_ sender: Any?) {
        guard let text = NSPasteboard.general.string(forType: .string), !text.isEmpty else { return }
        // Le collage entre crochets permet au shell distant de distinguer un
        // bloc collé d'une frappe : sans lui, un texte multiligne s'exécute.
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\r")
            .replacingOccurrences(of: "\n", with: "\r")
        scrollToBottom()
        if emulator.bracketedPaste {
            onInput?(Data("\u{1B}[200~".utf8) + Data(normalized.utf8) + Data("\u{1B}[201~".utf8))
        } else {
            onInput?(Data(normalized.utf8))
        }
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        let menu = NSMenu()
        let copyItem = NSMenuItem(title: "Copier", action: #selector(copySelection(_:)), keyEquivalent: "")
        copyItem.isEnabled = hasSelection
        copyItem.target = self
        menu.addItem(copyItem)
        let pasteItem = NSMenuItem(title: "Coller", action: #selector(pasteFromPasteboard(_:)), keyEquivalent: "")
        pasteItem.target = self
        menu.addItem(pasteItem)
        menu.addItem(.separator())
        let clearItem = NSMenuItem(title: "Effacer l'écran", action: #selector(clearScreen(_:)), keyEquivalent: "")
        clearItem.target = self
        menu.addItem(clearItem)
        return menu
    }

    @objc func clearScreen(_ sender: Any?) {
        selectionAnchor = nil
        selectionHead = nil
        scrollToBottom()
        onClear?()
    }

    // MARK: - Clavier

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard flags.contains(.command), !flags.contains(.control) else { return false }
        switch event.charactersIgnoringModifiers?.lowercased() {
        case "c":
            guard hasSelection else { return false }
            copySelection(nil)
            return true
        case "v":
            pasteFromPasteboard(nil)
            return true
        case "k":
            clearScreen(nil)
            return true
        case "+", "=":
            adjustFontSize(1)
            return true
        case "-":
            adjustFontSize(-1)
            return true
        default:
            return false
        }
    }

    override func keyDown(with event: NSEvent) {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)

        // ⇧ Page haut / bas fait défiler l'historique local, sans rien envoyer.
        if flags.contains(.shift), let special = event.specialKey {
            if special == .pageUp {
                setScrollOffset(scrollOffset + emulator.rows - 1)
                return
            }
            if special == .pageDown {
                setScrollOffset(scrollOffset - emulator.rows + 1)
                return
            }
        }

        if let data = TerminalKeys.encode(
            event: event,
            applicationCursorKeys: emulator.applicationCursorKeys,
            optionSendsMeta: optionSendsMeta
        ) {
            scrollToBottom()
            onInput?(data)
            return
        }
        interpretKeyEvents([event])
    }

    override func doCommand(by selector: Selector) {
        // Les touches spéciales sont déjà encodées : ici, tout appel serait un
        // bip système sans effet utile.
    }

    // MARK: - NSTextInputClient

    func insertText(_ string: Any, replacementRange: NSRange) {
        markedText = ""
        let text = (string as? String) ?? (string as? NSAttributedString)?.string ?? ""
        guard !text.isEmpty else { return }
        scrollToBottom()
        onInput?(Data(text.utf8))
    }

    func setMarkedText(_ string: Any, selectedRange: NSRange, replacementRange: NSRange) {
        markedText = (string as? String) ?? (string as? NSAttributedString)?.string ?? ""
    }

    func unmarkText() { markedText = "" }
    func selectedRange() -> NSRange { NSRange(location: NSNotFound, length: 0) }
    func markedRange() -> NSRange {
        markedText.isEmpty ? NSRange(location: NSNotFound, length: 0) : NSRange(location: 0, length: markedText.count)
    }
    func hasMarkedText() -> Bool { !markedText.isEmpty }
    func attributedSubstring(forProposedRange range: NSRange, actualRange: NSRangePointer?) -> NSAttributedString? { nil }
    func validAttributesForMarkedText() -> [NSAttributedString.Key] { [] }
    func characterIndex(for point: NSPoint) -> Int { NSNotFound }

    func firstRect(forCharacterRange range: NSRange, actualRange: NSRangePointer?) -> NSRect {
        let origin = NSPoint(
            x: padding + CGFloat(emulator.cursor.column) * cellSize.width,
            y: padding + CGFloat(emulator.cursor.row) * cellSize.height
        )
        let rect = NSRect(origin: origin, size: cellSize)
        return window?.convertToScreen(convert(rect, to: nil)) ?? rect
    }
}

/// Traduction des touches en octets VT. Séparée de la vue pour rester lisible :
/// c'est une table, pas une logique.
enum TerminalKeys {
    static func encode(event: NSEvent, applicationCursorKeys: Bool, optionSendsMeta: Bool) -> Data? {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        // ⌘ appartient aux menus et aux raccourcis de la vue.
        guard !flags.contains(.command) else { return nil }

        let control = flags.contains(.control)
        let meta = optionSendsMeta && flags.contains(.option)

        if event.keyCode == 53 { return Data([0x1B]) } // Échap

        if let special = event.specialKey,
           let bytes = encode(special: special, flags: flags, applicationCursorKeys: applicationCursorKeys) {
            return meta ? Data([0x1B]) + bytes : bytes
        }

        if control, let byte = controlByte(event.charactersIgnoringModifiers) {
            return meta ? Data([0x1B, byte]) : Data([byte])
        }

        if meta, let characters = event.charactersIgnoringModifiers, !characters.isEmpty {
            return Data([0x1B]) + Data(characters.utf8)
        }

        return nil // texte ordinaire : laissé à la composition du système
    }

    private static func encode(
        special: NSEvent.SpecialKey,
        flags: NSEvent.ModifierFlags,
        applicationCursorKeys: Bool
    ) -> Data? {
        func arrow(_ letter: String) -> Data {
            if let modifier = modifierParameter(flags) {
                return Data("\u{1B}[1;\(modifier)\(letter)".utf8)
            }
            return Data((applicationCursorKeys ? "\u{1B}O\(letter)" : "\u{1B}[\(letter)").utf8)
        }
        func tilde(_ code: Int) -> Data {
            if let modifier = modifierParameter(flags) {
                return Data("\u{1B}[\(code);\(modifier)~".utf8)
            }
            return Data("\u{1B}[\(code)~".utf8)
        }

        switch special {
        case .upArrow: return arrow("A")
        case .downArrow: return arrow("B")
        case .rightArrow: return arrow("C")
        case .leftArrow: return arrow("D")
        case .home: return Data("\u{1B}[H".utf8)
        case .end: return Data("\u{1B}[F".utf8)
        case .pageUp: return tilde(5)
        case .pageDown: return tilde(6)
        case .insert: return tilde(2)
        case .deleteForward: return tilde(3)
        case .delete: return Data([0x7F])
        case .backspace: return Data([0x08])
        case .carriageReturn, .enter, .newline: return Data([0x0D])
        case .tab: return Data([0x09])
        case .backTab: return Data("\u{1B}[Z".utf8)
        case .f1: return Data("\u{1B}OP".utf8)
        case .f2: return Data("\u{1B}OQ".utf8)
        case .f3: return Data("\u{1B}OR".utf8)
        case .f4: return Data("\u{1B}OS".utf8)
        case .f5: return tilde(15)
        case .f6: return tilde(17)
        case .f7: return tilde(18)
        case .f8: return tilde(19)
        case .f9: return tilde(20)
        case .f10: return tilde(21)
        case .f11: return tilde(23)
        case .f12: return tilde(24)
        default: return nil
        }
    }

    /// Paramètre xterm des modificateurs : 1 + majuscule(1) + option(2) + contrôle(4).
    private static func modifierParameter(_ flags: NSEvent.ModifierFlags) -> Int? {
        var value = 1
        if flags.contains(.shift) { value += 1 }
        if flags.contains(.option) { value += 2 }
        if flags.contains(.control) { value += 4 }
        return value > 1 ? value : nil
    }

    private static func controlByte(_ characters: String?) -> UInt8? {
        guard let scalar = characters?.lowercased().unicodeScalars.first else { return nil }
        switch scalar {
        case "a"..."z":
            return UInt8(scalar.value - 0x60)
        case " ", "@":
            return 0
        case "[":
            return 0x1B
        case "\\":
            return 0x1C
        case "]":
            return 0x1D
        case "^":
            return 0x1E
        case "_", "/":
            return 0x1F
        case "?":
            return 0x7F
        default:
            return nil
        }
    }
}
