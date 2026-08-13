import Foundation

/// Couleur d'une cellule. `default` laisse la vue choisir la couleur du thème :
/// le moteur ne connaît aucune valeur RVB de la charte.
enum TerminalColor: Equatable, Sendable {
    case `default`
    case indexed(UInt8)
    case rgb(UInt8, UInt8, UInt8)
}

struct TerminalAttributes: Equatable, Sendable {
    var foreground: TerminalColor = .default
    var background: TerminalColor = .default
    var bold = false
    var dim = false
    var italic = false
    var underline = false
    var inverse = false
    var hidden = false
    var strikethrough = false
}

struct TerminalCell: Equatable, Sendable {
    var character: Character = " "
    var attributes = TerminalAttributes()
    /// Première moitié d'un glyphe double largeur (CJK, emoji).
    var isWide = false
    /// Deuxième moitié : la cellule existe pour la géométrie, jamais dessinée.
    var isContinuation = false

    static let blank = TerminalCell()
}

/// Émulateur VT100 / xterm réduit au sous-ensemble qu'une session SSH
/// interactive utilise réellement : attributs SGR, régions de défilement,
/// écran alterné, retour à la ligne différé, historique.
///
/// Ce qu'il ne fait pas, volontairement : reflow au redimensionnement,
/// rapport de souris, Sixel, jeux de caractères autres qu'ASCII/UTF-8. Une
/// ligne coupée reste coupée si la fenêtre s'élargit — comme dans Terminal.app
/// avant le reflow, et sans le coût d'une réécriture de l'historique.
@MainActor
final class TerminalEmulator {
    struct Cursor: Equatable {
        var row = 0
        var column = 0
        var visible = true
    }

    private struct SavedCursor {
        var row = 0
        var column = 0
        var attributes = TerminalAttributes()
        var originMode = false
    }

    private enum ParserState {
        case ground
        case escape
        case csi
        case osc
        /// DCS / APC / PM : consommés jusqu'au terminateur de chaîne.
        case string
        /// Octet de désignation de jeu de caractères, ignoré.
        case charset
    }

    // MARK: - État public

    private(set) var columns: Int
    private(set) var rows: Int
    private(set) var screen: [[TerminalCell]]
    private(set) var scrollback: [[TerminalCell]] = []
    private(set) var cursor = Cursor()
    private(set) var title = ""
    /// Mode curseur applicatif : les flèches émettent `ESC O A` et non `ESC [ A`.
    private(set) var applicationCursorKeys = false
    /// Collage entre crochets : le shell distant sait qu'un bloc est collé.
    private(set) var bracketedPaste = false
    private(set) var isAlternateScreen = false

    /// Observateurs de rendu. Une liste et non un rappel unique : SwiftUI
    /// reconstruit la vue hôte à chaque changement structurel, et une seconde
    /// fenêtre est légitime — le dernier inscrit ne doit pas éteindre les
    /// autres. Le propriétaire est retenu faiblement, ce qui évite un
    /// désabonnement en `deinit` que l'isolation d'acteur interdit.
    private struct ScreenObserver {
        weak var owner: AnyObject?
        let handler: () -> Void
    }

    private var screenObservers: [ScreenObserver] = []

    func addScreenObserver(owner: AnyObject, handler: @escaping () -> Void) {
        screenObservers.removeAll { $0.owner == nil || $0.owner === owner }
        screenObservers.append(ScreenObserver(owner: owner, handler: handler))
    }

    private func notifyScreenChange() {
        screenObservers.removeAll { $0.owner == nil }
        for observer in screenObservers { observer.handler() }
    }

    /// Réponses du terminal (DSR, DA) à réinjecter dans le PTY.
    var onResponse: ((Data) -> Void)?
    var onTitleChange: ((String) -> Void)?

    let scrollbackLimit = 5_000

    // MARK: - État interne

    private var state: ParserState = .ground
    private var csiBuffer: [UInt8] = []
    private var oscBuffer: [UInt8] = []
    private var oscEscapePending = false
    private var stringEscapePending = false

    private var utf8Buffer: [UInt8] = []
    private var utf8Remaining = 0

    private var attributes = TerminalAttributes()
    private var savedCursor = SavedCursor()
    private var savedPrimaryScreen: [[TerminalCell]] = []
    private var savedPrimaryCursor = SavedCursor()

    private var scrollTop = 0
    private var scrollBottom: Int
    private var autowrap = true
    private var originMode = false
    private var insertMode = false
    /// Retour à la ligne différé : le curseur reste sur la dernière colonne
    /// tant qu'aucun caractère n'est écrit. Sans cela, une ligne exactement
    /// large comme l'écran saute une ligne de trop.
    private var wrapPending = false

    // MARK: - Cycle de vie

    init(columns: Int = 80, rows: Int = 24) {
        let safeColumns = max(2, columns)
        let safeRows = max(1, rows)
        self.columns = safeColumns
        self.rows = safeRows
        self.scrollBottom = safeRows - 1
        self.screen = (0..<safeRows).map { _ in
            [TerminalCell](repeating: .blank, count: safeColumns)
        }
    }

    func reset() {
        screen = (0..<rows).map { _ in blankLine() }
        scrollback.removeAll()
        cursor = Cursor()
        attributes = TerminalAttributes()
        savedCursor = SavedCursor()
        scrollTop = 0
        scrollBottom = rows - 1
        autowrap = true
        originMode = false
        insertMode = false
        wrapPending = false
        applicationCursorKeys = false
        bracketedPaste = false
        isAlternateScreen = false
        savedPrimaryScreen = []
        state = .ground
        csiBuffer.removeAll()
        oscBuffer.removeAll()
        utf8Buffer.removeAll()
        utf8Remaining = 0
        notifyScreenChange()
    }

    /// Efface l'écran visible et l'historique, sans toucher à la session.
    func clearAll() {
        screen = (0..<rows).map { _ in blankLine() }
        scrollback.removeAll()
        cursor.row = 0
        cursor.column = 0
        wrapPending = false
        notifyScreenChange()
    }

    func resize(columns newColumns: Int, rows newRows: Int) {
        let safeColumns = max(2, newColumns)
        let safeRows = max(1, newRows)
        guard safeColumns != columns || safeRows != rows else { return }

        for index in screen.indices {
            if screen[index].count > safeColumns {
                screen[index] = Array(screen[index].prefix(safeColumns))
            } else if screen[index].count < safeColumns {
                screen[index].append(
                    contentsOf: [TerminalCell](
                        repeating: .blank,
                        count: safeColumns - screen[index].count
                    )
                )
            }
        }

        if safeRows < screen.count {
            // On sacrifie le haut de l'écran, pas le bas : ce que l'utilisateur
            // vient de taper doit rester visible.
            let excess = screen.count - safeRows
            let removed = screen.prefix(excess)
            if !isAlternateScreen { removed.forEach { appendScrollback($0) } }
            screen.removeFirst(excess)
            cursor.row = max(0, cursor.row - excess)
        } else if safeRows > screen.count {
            for _ in 0..<(safeRows - screen.count) {
                screen.append([TerminalCell](repeating: .blank, count: safeColumns))
            }
        }

        columns = safeColumns
        rows = safeRows
        scrollTop = 0
        scrollBottom = safeRows - 1
        cursor.row = min(cursor.row, safeRows - 1)
        cursor.column = min(cursor.column, safeColumns - 1)
        wrapPending = false
        notifyScreenChange()
    }

    // MARK: - Entrée

    func feed(_ data: Data) {
        guard !data.isEmpty else { return }
        for byte in data { process(byte) }
        notifyScreenChange()
    }

    // MARK: - Machine à états

    private func process(_ byte: UInt8) {
        switch state {
        case .ground: ground(byte)
        case .escape: escape(byte)
        case .csi: csi(byte)
        case .osc: osc(byte)
        case .string: stringSequence(byte)
        case .charset: state = .ground
        }
    }

    private func ground(_ byte: UInt8) {
        if byte == 0x1B {
            utf8Buffer.removeAll(keepingCapacity: true)
            utf8Remaining = 0
            state = .escape
            return
        }
        if utf8Remaining > 0 {
            guard byte & 0xC0 == 0x80 else {
                // Séquence tronquée : on repart proprement plutôt que d'écrire
                // un caractère faux.
                utf8Buffer.removeAll(keepingCapacity: true)
                utf8Remaining = 0
                ground(byte)
                return
            }
            utf8Buffer.append(byte)
            utf8Remaining -= 1
            if utf8Remaining == 0 { flushUTF8() }
            return
        }
        if byte < 0x20 || byte == 0x7F {
            control(byte)
            return
        }
        if byte < 0x80 {
            put(Character(UnicodeScalar(byte)))
            return
        }
        utf8Buffer = [byte]
        switch byte {
        case 0xC2...0xDF: utf8Remaining = 1
        case 0xE0...0xEF: utf8Remaining = 2
        case 0xF0...0xF4: utf8Remaining = 3
        default:
            utf8Buffer.removeAll(keepingCapacity: true)
            utf8Remaining = 0
        }
    }

    private func flushUTF8() {
        defer {
            utf8Buffer.removeAll(keepingCapacity: true)
            utf8Remaining = 0
        }
        guard let scalar = String(decoding: utf8Buffer, as: UTF8.self).unicodeScalars.first,
              scalar != "\u{FFFD}" else { return }
        if scalar.properties.isGraphemeExtend || scalar.properties.canonicalCombiningClass != .notReordered {
            appendCombining(scalar)
        } else {
            put(Character(scalar))
        }
    }

    private func control(_ byte: UInt8) {
        switch byte {
        case 0x07: break // cloche : consommée sans bruit
        case 0x08:
            cursor.column = max(0, cursor.column - 1)
            wrapPending = false
        case 0x09:
            let next = ((cursor.column / 8) + 1) * 8
            cursor.column = min(next, columns - 1)
            wrapPending = false
        case 0x0A, 0x0B, 0x0C:
            lineFeed()
        case 0x0D:
            cursor.column = 0
            wrapPending = false
        default:
            break
        }
    }

    private func escape(_ byte: UInt8) {
        state = .ground
        switch byte {
        case 0x5B: // [
            csiBuffer.removeAll(keepingCapacity: true)
            state = .csi
        case 0x5D: // ]
            oscBuffer.removeAll(keepingCapacity: true)
            oscEscapePending = false
            state = .osc
        case 0x50, 0x58, 0x5E, 0x5F: // P X ^ _
            stringEscapePending = false
            state = .string
        case 0x28, 0x29, 0x2A, 0x2B: // ( ) * +
            state = .charset
        case 0x37: saveCursor()          // 7
        case 0x38: restoreCursor()       // 8
        case 0x44: index()               // D
        case 0x45:                       // E
            index()
            cursor.column = 0
        case 0x4D: reverseIndex()        // M
        case 0x63: reset()               // c
        default: break                   // = > \ et le reste : sans effet ici
        }
    }

    private func csi(_ byte: UInt8) {
        if byte >= 0x40 && byte <= 0x7E {
            dispatchCSI(final: byte)
            csiBuffer.removeAll(keepingCapacity: true)
            state = .ground
            return
        }
        if csiBuffer.count < 128 { csiBuffer.append(byte) }
    }

    private func osc(_ byte: UInt8) {
        if byte == 0x07 {
            finishOSC()
            return
        }
        if oscEscapePending {
            oscEscapePending = false
            if byte == 0x5C {
                finishOSC()
                return
            }
        }
        if byte == 0x1B {
            oscEscapePending = true
            return
        }
        if oscBuffer.count < 1_024 { oscBuffer.append(byte) }
    }

    private func stringSequence(_ byte: UInt8) {
        if byte == 0x07 {
            state = .ground
            return
        }
        if stringEscapePending {
            stringEscapePending = false
            if byte == 0x5C {
                state = .ground
                return
            }
        }
        if byte == 0x1B { stringEscapePending = true }
    }

    private func finishOSC() {
        defer {
            oscBuffer.removeAll(keepingCapacity: true)
            oscEscapePending = false
            state = .ground
        }
        let payload = String(decoding: oscBuffer, as: UTF8.self)
        guard let separator = payload.firstIndex(of: ";") else { return }
        let code = String(payload[payload.startIndex..<separator])
        let value = String(payload[payload.index(after: separator)...])
        guard code == "0" || code == "1" || code == "2" else { return }
        title = value
        onTitleChange?(value)
    }

    // MARK: - CSI

    private func dispatchCSI(final: UInt8) {
        var bytes = csiBuffer
        var privatePrefix: UInt8?
        if let first = bytes.first, first >= 0x3C, first <= 0x3F {
            privatePrefix = first
            bytes.removeFirst()
        }
        // Un intermédiaire (espace, `$`, `"`…) désigne une séquence que ce
        // moteur ne gère pas : l'ignorer vaut mieux que la confondre.
        guard !bytes.contains(where: { $0 >= 0x20 && $0 <= 0x2F }) else { return }

        let params = parseParameters(bytes)
        func param(_ index: Int, default fallback: Int) -> Int {
            guard index < params.count, let value = params[index], value > 0 else { return fallback }
            return value
        }

        if privatePrefix == 0x3F {
            switch final {
            case 0x68: setPrivateModes(params, enabled: true)   // h
            case 0x6C: setPrivateModes(params, enabled: false)  // l
            default: break
            }
            return
        }
        guard privatePrefix == nil else { return }

        switch final {
        case 0x40: insertCharacters(param(0, default: 1))                    // @
        case 0x41: moveCursor(rows: -param(0, default: 1))                   // A
        case 0x42: moveCursor(rows: param(0, default: 1))                    // B
        case 0x43: moveCursor(columns: param(0, default: 1))                 // C
        case 0x44: moveCursor(columns: -param(0, default: 1))                // D
        case 0x45:                                                           // E
            moveCursor(rows: param(0, default: 1))
            cursor.column = 0
        case 0x46:                                                           // F
            moveCursor(rows: -param(0, default: 1))
            cursor.column = 0
        case 0x47, 0x60:                                                     // G `
            cursor.column = clampColumn(param(0, default: 1) - 1)
            wrapPending = false
        case 0x48, 0x66:                                                     // H f
            setCursorPosition(row: param(0, default: 1) - 1, column: param(1, default: 1) - 1)
        case 0x4A: eraseInDisplay(params.first.flatMap { $0 } ?? 0)          // J
        case 0x4B: eraseInLine(params.first.flatMap { $0 } ?? 0)             // K
        case 0x4C: insertLines(param(0, default: 1))                         // L
        case 0x4D: deleteLines(param(0, default: 1))                         // M
        case 0x50: deleteCharacters(param(0, default: 1))                    // P
        case 0x53: scrollUp(param(0, default: 1))                            // S
        case 0x54: scrollDown(param(0, default: 1))                          // T
        case 0x58: eraseCharacters(param(0, default: 1))                     // X
        case 0x61: moveCursor(columns: param(0, default: 1))                 // a
        case 0x64:                                                           // d
            cursor.row = clampRow(param(0, default: 1) - 1)
            wrapPending = false
        case 0x65: moveCursor(rows: param(0, default: 1))                    // e
        case 0x68: setModes(params, enabled: true)                           // h
        case 0x6C: setModes(params, enabled: false)                          // l
        case 0x6D: applySGR(params)                                          // m
        case 0x6E: deviceStatusReport(params.first.flatMap { $0 } ?? 0)      // n
        case 0x63: onResponse?(Data("\u{1B}[?1;2c".utf8))                    // c
        case 0x72:                                                           // r
            setScrollRegion(top: param(0, default: 1) - 1, bottom: param(1, default: rows) - 1)
        case 0x73: saveCursor()                                              // s
        case 0x75: restoreCursor()                                           // u
        default: break
        }
    }

    /// `;` et `:` sont aplatis dans la même liste : cela couvre `38;5;n`,
    /// `38;2;r;g;b` et la forme à deux-points `38:2::r:g:b`, dont les champs
    /// vides deviennent des `nil` que le lecteur SGR saute.
    private func parseParameters(_ bytes: [UInt8]) -> [Int?] {
        var params: [Int?] = []
        var current: Int?
        for byte in bytes {
            if byte >= 0x30 && byte <= 0x39 {
                current = min((current ?? 0) * 10 + Int(byte - 0x30), 65_535)
            } else if byte == 0x3B || byte == 0x3A {
                params.append(current)
                current = nil
            }
        }
        params.append(current)
        return params
    }

    private func setModes(_ params: [Int?], enabled: Bool) {
        for case let mode? in params where mode == 4 { insertMode = enabled }
    }

    private func setPrivateModes(_ params: [Int?], enabled: Bool) {
        for case let mode? in params {
            switch mode {
            case 1: applicationCursorKeys = enabled
            case 6:
                originMode = enabled
                setCursorPosition(row: 0, column: 0)
            case 7: autowrap = enabled
            case 25: cursor.visible = enabled
            case 47, 1047, 1049: setAlternateScreen(enabled, saveCursor: mode == 1049)
            case 2004: bracketedPaste = enabled
            default: break // souris, clignotement, focus : acceptés sans effet
            }
        }
    }

    private func deviceStatusReport(_ code: Int) {
        switch code {
        case 5:
            onResponse?(Data("\u{1B}[0n".utf8))
        case 6:
            let row = (originMode ? cursor.row - scrollTop : cursor.row) + 1
            onResponse?(Data("\u{1B}[\(row);\(cursor.column + 1)R".utf8))
        default:
            break
        }
    }

    private func applySGR(_ params: [Int?]) {
        guard !params.isEmpty else {
            attributes = TerminalAttributes()
            return
        }
        var index = 0
        while index < params.count {
            let code = params[index] ?? 0
            switch code {
            case 0: attributes = TerminalAttributes()
            case 1: attributes.bold = true
            case 2: attributes.dim = true
            case 3: attributes.italic = true
            case 4: attributes.underline = true
            case 7: attributes.inverse = true
            case 8: attributes.hidden = true
            case 9: attributes.strikethrough = true
            case 21, 22:
                attributes.bold = false
                attributes.dim = false
            case 23: attributes.italic = false
            case 24: attributes.underline = false
            case 27: attributes.inverse = false
            case 28: attributes.hidden = false
            case 29: attributes.strikethrough = false
            case 30...37: attributes.foreground = .indexed(UInt8(code - 30))
            case 39: attributes.foreground = .default
            case 40...47: attributes.background = .indexed(UInt8(code - 40))
            case 49: attributes.background = .default
            case 90...97: attributes.foreground = .indexed(UInt8(code - 90 + 8))
            case 100...107: attributes.background = .indexed(UInt8(code - 100 + 8))
            case 38, 48:
                let (color, consumed) = extendedColor(params, from: index + 1)
                if let color {
                    if code == 38 { attributes.foreground = color } else { attributes.background = color }
                }
                index += consumed
            default: break
            }
            index += 1
        }
    }

    private func extendedColor(_ params: [Int?], from start: Int) -> (TerminalColor?, Int) {
        var values: [Int] = []
        var index = start
        while index < params.count && values.count < 4 {
            if let value = params[index] { values.append(value) }
            index += 1
            if values.first == 5 && values.count == 2 { break }
            if values.first == 2 && values.count == 4 { break }
        }
        let consumed = index - start
        guard let kind = values.first else { return (nil, consumed) }
        if kind == 5, values.count >= 2 {
            return (.indexed(UInt8(clamping: values[1])), consumed)
        }
        if kind == 2, values.count >= 4 {
            return (
                .rgb(UInt8(clamping: values[1]), UInt8(clamping: values[2]), UInt8(clamping: values[3])),
                consumed
            )
        }
        return (nil, consumed)
    }

    // MARK: - Écriture

    private func put(_ character: Character) {
        let width = max(1, terminalCharacterWidth(character))
        if wrapPending && autowrap {
            cursor.column = 0
            lineFeed()
        }
        wrapPending = false

        if cursor.column + width > columns {
            if autowrap {
                cursor.column = 0
                lineFeed()
            } else {
                cursor.column = columns - width
            }
        }

        if insertMode {
            insertCharacters(width)
        }

        var cell = TerminalCell()
        cell.character = character
        cell.attributes = attributes
        cell.isWide = width == 2
        screen[cursor.row][cursor.column] = cell
        if width == 2 && cursor.column + 1 < columns {
            var continuation = TerminalCell()
            continuation.attributes = attributes
            continuation.isContinuation = true
            screen[cursor.row][cursor.column + 1] = continuation
        }

        cursor.column += width
        if cursor.column >= columns {
            cursor.column = columns - 1
            wrapPending = true
        }
    }

    /// Un diacritique n'occupe pas de cellule : il complète le glyphe précédent.
    private func appendCombining(_ scalar: UnicodeScalar) {
        var column = cursor.column
        if wrapPending { column = columns - 1 } else { column -= 1 }
        while column >= 0 && screen[cursor.row][column].isContinuation { column -= 1 }
        guard column >= 0 else { return }
        var text = String(screen[cursor.row][column].character)
        text.unicodeScalars.append(scalar)
        guard let combined = text.first else { return }
        screen[cursor.row][column].character = combined
    }

    // MARK: - Curseur et défilement

    private func blankLine() -> [TerminalCell] {
        [TerminalCell](repeating: .blank, count: columns)
    }

    private func clampRow(_ row: Int) -> Int { min(max(row, 0), rows - 1) }
    private func clampColumn(_ column: Int) -> Int { min(max(column, 0), columns - 1) }

    private func setCursorPosition(row: Int, column: Int) {
        let target = originMode ? row + scrollTop : row
        cursor.row = originMode ? min(clampRow(target), scrollBottom) : clampRow(target)
        cursor.column = clampColumn(column)
        wrapPending = false
    }

    private func moveCursor(rows delta: Int = 0, columns columnDelta: Int = 0) {
        if delta != 0 {
            // Un déplacement vertical ne franchit pas la région de défilement.
            let lower = cursor.row >= scrollTop ? scrollTop : 0
            let upper = cursor.row <= scrollBottom ? scrollBottom : rows - 1
            cursor.row = min(max(cursor.row + delta, lower), upper)
        }
        if columnDelta != 0 {
            cursor.column = clampColumn(cursor.column + columnDelta)
        }
        wrapPending = false
    }

    private func saveCursor() {
        savedCursor = SavedCursor(
            row: cursor.row,
            column: cursor.column,
            attributes: attributes,
            originMode: originMode
        )
    }

    private func restoreCursor() {
        cursor.row = clampRow(savedCursor.row)
        cursor.column = clampColumn(savedCursor.column)
        attributes = savedCursor.attributes
        originMode = savedCursor.originMode
        wrapPending = false
    }

    private func setScrollRegion(top: Int, bottom: Int) {
        let newTop = clampRow(top)
        let newBottom = clampRow(bottom)
        guard newTop < newBottom else { return }
        scrollTop = newTop
        scrollBottom = newBottom
        setCursorPosition(row: 0, column: 0)
    }

    private func lineFeed() {
        if cursor.row == scrollBottom {
            scrollUp(1)
        } else if cursor.row < rows - 1 {
            cursor.row += 1
        }
        wrapPending = false
    }

    private func index() { lineFeed() }

    private func reverseIndex() {
        if cursor.row == scrollTop {
            scrollDown(1)
        } else if cursor.row > 0 {
            cursor.row -= 1
        }
        wrapPending = false
    }

    private func scrollUp(_ count: Int) {
        guard count > 0 else { return }
        for _ in 0..<min(count, rows) {
            let removed = screen[scrollTop]
            if !isAlternateScreen && scrollTop == 0 { appendScrollback(removed) }
            screen.remove(at: scrollTop)
            screen.insert(blankLine(), at: scrollBottom)
        }
    }

    private func scrollDown(_ count: Int) {
        guard count > 0 else { return }
        for _ in 0..<min(count, rows) {
            screen.remove(at: scrollBottom)
            screen.insert(blankLine(), at: scrollTop)
        }
    }

    private func appendScrollback(_ line: [TerminalCell]) {
        scrollback.append(line)
        if scrollback.count > scrollbackLimit {
            scrollback.removeFirst(scrollback.count - scrollbackLimit)
        }
    }

    private func setAlternateScreen(_ enabled: Bool, saveCursor useSavedCursor: Bool) {
        guard enabled != isAlternateScreen else { return }
        if enabled {
            savedPrimaryScreen = screen
            savedPrimaryCursor = SavedCursor(
                row: cursor.row,
                column: cursor.column,
                attributes: attributes,
                originMode: originMode
            )
            screen = (0..<rows).map { _ in blankLine() }
            cursor.row = 0
            cursor.column = 0
            isAlternateScreen = true
        } else {
            screen = savedPrimaryScreen.isEmpty
                ? (0..<rows).map { _ in blankLine() }
                : savedPrimaryScreen
            // Le contenu sauvegardé peut dater d'une autre géométrie.
            resizeRestoredScreen()
            if useSavedCursor {
                cursor.row = clampRow(savedPrimaryCursor.row)
                cursor.column = clampColumn(savedPrimaryCursor.column)
                attributes = savedPrimaryCursor.attributes
                originMode = savedPrimaryCursor.originMode
            }
            savedPrimaryScreen = []
            isAlternateScreen = false
        }
        wrapPending = false
    }

    private func resizeRestoredScreen() {
        for index in screen.indices where screen[index].count != columns {
            if screen[index].count > columns {
                screen[index] = Array(screen[index].prefix(columns))
            } else {
                screen[index].append(
                    contentsOf: [TerminalCell](
                        repeating: .blank,
                        count: columns - screen[index].count
                    )
                )
            }
        }
        if screen.count > rows {
            screen.removeLast(screen.count - rows)
        } else if screen.count < rows {
            for _ in 0..<(rows - screen.count) { screen.append(blankLine()) }
        }
    }

    // MARK: - Effacements et insertions

    private func blankCell() -> TerminalCell {
        // L'effacement conserve le fond courant : c'est ainsi qu'un `clear`
        // sous thème coloré repeint réellement l'écran.
        var cell = TerminalCell()
        cell.attributes.background = attributes.background
        return cell
    }

    private func eraseInDisplay(_ mode: Int) {
        switch mode {
        case 0:
            eraseInLine(0)
            for row in (cursor.row + 1)..<rows {
                screen[row] = [TerminalCell](repeating: blankCell(), count: columns)
            }
        case 1:
            eraseInLine(1)
            for row in 0..<cursor.row {
                screen[row] = [TerminalCell](repeating: blankCell(), count: columns)
            }
        case 2, 3:
            for row in 0..<rows {
                screen[row] = [TerminalCell](repeating: blankCell(), count: columns)
            }
            if mode == 3 { scrollback.removeAll() }
        default:
            break
        }
        wrapPending = false
    }

    private func eraseInLine(_ mode: Int) {
        switch mode {
        case 0:
            for column in cursor.column..<columns { screen[cursor.row][column] = blankCell() }
        case 1:
            for column in 0...min(cursor.column, columns - 1) { screen[cursor.row][column] = blankCell() }
        case 2:
            screen[cursor.row] = [TerminalCell](repeating: blankCell(), count: columns)
        default:
            break
        }
        wrapPending = false
    }

    private func eraseCharacters(_ count: Int) {
        let end = min(cursor.column + count, columns)
        guard cursor.column < end else { return }
        for column in cursor.column..<end { screen[cursor.row][column] = blankCell() }
    }

    private func insertCharacters(_ count: Int) {
        let amount = min(count, columns - cursor.column)
        guard amount > 0 else { return }
        var line = screen[cursor.row]
        line.removeLast(amount)
        line.insert(contentsOf: [TerminalCell](repeating: blankCell(), count: amount), at: cursor.column)
        screen[cursor.row] = line
    }

    private func deleteCharacters(_ count: Int) {
        let amount = min(count, columns - cursor.column)
        guard amount > 0 else { return }
        var line = screen[cursor.row]
        line.removeSubrange(cursor.column..<(cursor.column + amount))
        line.append(contentsOf: [TerminalCell](repeating: blankCell(), count: amount))
        screen[cursor.row] = line
    }

    private func insertLines(_ count: Int) {
        guard cursor.row >= scrollTop, cursor.row <= scrollBottom else { return }
        let amount = min(count, scrollBottom - cursor.row + 1)
        for _ in 0..<amount {
            screen.remove(at: scrollBottom)
            screen.insert(blankLine(), at: cursor.row)
        }
        cursor.column = 0
        wrapPending = false
    }

    private func deleteLines(_ count: Int) {
        guard cursor.row >= scrollTop, cursor.row <= scrollBottom else { return }
        let amount = min(count, scrollBottom - cursor.row + 1)
        for _ in 0..<amount {
            screen.remove(at: cursor.row)
            screen.insert(blankLine(), at: scrollBottom)
        }
        cursor.column = 0
        wrapPending = false
    }

    // MARK: - Lecture pour le rendu et la sélection

    /// Ligne `index` de l'ensemble historique + écran, du plus ancien au plus
    /// récent. La vue n'a ainsi qu'un seul système de coordonnées.
    func line(at index: Int) -> [TerminalCell]? {
        if index < scrollback.count { return scrollback[index] }
        let screenIndex = index - scrollback.count
        guard screenIndex >= 0, screenIndex < screen.count else { return nil }
        return screen[screenIndex]
    }

    var totalLines: Int { scrollback.count + screen.count }
    var firstScreenLine: Int { scrollback.count }

    /// Texte d'une plage de cellules, espaces de fin retirés ligne par ligne.
    func text(from start: (line: Int, column: Int), to end: (line: Int, column: Int)) -> String {
        var result: [String] = []
        for lineIndex in start.line...max(start.line, end.line) {
            guard let cells = line(at: lineIndex) else { continue }
            let lower = lineIndex == start.line ? min(start.column, cells.count) : 0
            let upper = lineIndex == end.line ? min(end.column, cells.count) : cells.count
            guard lower < upper else {
                result.append("")
                continue
            }
            var text = ""
            for cell in cells[lower..<upper] where !cell.isContinuation {
                text.append(cell.character)
            }
            while text.hasSuffix(" ") { text.removeLast() }
            result.append(text)
        }
        return result.joined(separator: "\n")
    }
}

/// Largeur d'affichage approximative, suffisante pour aligner les invites
/// modernes : deux colonnes pour les idéogrammes et les emoji, une sinon.
func terminalCharacterWidth(_ character: Character) -> Int {
    guard let scalar = character.unicodeScalars.first else { return 1 }
    if character.unicodeScalars.contains(where: { $0.properties.isEmojiPresentation }) { return 2 }
    switch scalar.value {
    case 0x1100...0x115F, 0x2E80...0x303E, 0x3041...0x33FF, 0x3400...0x4DBF,
         0x4E00...0x9FFF, 0xA000...0xA4CF, 0xAC00...0xD7A3, 0xF900...0xFAFF,
         0xFE10...0xFE19, 0xFE30...0xFE6F, 0xFF00...0xFF60, 0xFFE0...0xFFE6,
         0x1F300...0x1F64F, 0x1F680...0x1F6FF, 0x1F900...0x1F9FF, 0x20000...0x3FFFD:
        return 2
    default:
        return 1
    }
}
