import AppKit
import SwiftUI

/// Section « Missions Jarvis » — pilotage du moteur agentique.
///
/// Deux panneaux natifs : navigation/liste et détail. Ne pas imbriquer un
/// second `NavigationSplitView` dans celui de `RootView` : SwiftUI décale
/// alors les sidebars hors écran aux largeurs usuelles.
struct TasksView: View {
    @StateObject private var store: TaskControlStore

    @State private var isCreating = false
    @State private var isCancelConfirmPresented = false

    init(api: JarvisAPI) {
        _store = StateObject(wrappedValue: TaskControlStore(api: api))
    }

    var body: some View {
        HSplitView {
            missionBrowser
                .frame(minWidth: 300, idealWidth: 340, maxWidth: 440)
            detailColumn
                .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationTitle("Missions Jarvis")
        .toolbar { toolbarItems }
        .sheet(isPresented: $isCreating) {
            NewMissionSheet { title, description, priority, dueAt, project, comment in
                Task {
                    await store.createTask(
                        title: title,
                        description: description,
                        priority: priority,
                        dueAt: dueAt,
                        projectID: project,
                        comment: comment
                    )
                }
            }
        }
        .confirmationDialog(
            "Annuler cette mission ?",
            isPresented: $isCancelConfirmPresented,
            titleVisibility: .visible
        ) {
            Button("Annuler la mission", role: .destructive) {
                Task { await store.cancelTask(reason: "Annulée depuis macOS") }
            }
            Button("Continuer", role: .cancel) {}
        } message: {
            Text("Jarvis interrompra l'exécution. Le travail déjà produit restera consultable.")
        }
        .onAppear {
            store.appear()
            TaskNotificationCenter.shared.requestAuthorization()
            TaskNotificationCenter.shared.onOpenTask = { taskID in
                store.section = .attention
                store.select(taskID: taskID)
            }
        }
        .onDisappear { store.disappear() }
        .alert(
            "Jarvis",
            isPresented: Binding(
                get: { store.errorMessage != nil },
                set: { if !$0 { store.errorMessage = nil } }
            )
        ) {
            Button("OK") { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    // MARK: - Navigation et liste

    private var missionBrowser: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                Label("MISSIONS JARVIS", systemImage: "gearshape.2.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(JarvisPalette.cyan)
                Text("Jarvis prépare un plan. Vous validez. Il exécute.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField("Rechercher une mission", text: $store.searchText)
                        .textFieldStyle(.plain)
                    if !store.searchText.isEmpty {
                        Button { store.searchText = "" } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Effacer la recherche")
                    }
                }
                .padding(9)
                .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(.regularMaterial)

            Divider()

            listHeader
            Divider()
            if store.section.isCandidateSection {
                candidateList
            } else {
                taskList
            }
        }
    }

    private var sectionMenu: some View {
        Menu {
            Section("À décider") {
                sectionButton(.toApprove)
                sectionButton(.attention)
            }
            Section("Suivi") {
                sectionButton(.planned)
                sectionButton(.running)
            }
            Section("Historique") {
                sectionButton(.completed)
                sectionButton(.failed)
                sectionButton(.archived)
            }
            Section("Suggestions") {
                sectionButton(.candidates)
            }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: store.section.symbol)
                    .font(.title3)
                    .foregroundStyle(store.section == .attention ? .orange : JarvisPalette.cyan)
                    .frame(width: 28, height: 28)
                    .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    Text(store.section.title).font(.headline)
                    Text(store.section.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 4)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .menuStyle(.borderlessButton)
        .accessibilityLabel("Filtrer les missions : \(store.section.title)")
    }

    private func sectionButton(_ section: TaskControlSection) -> some View {
        Button {
            select(section)
        } label: {
            HStack {
                Label(section.title, systemImage: section.symbol)
                Spacer()
                Text("\(store.count(for: section))")
                    .monospacedDigit()
            }
        }
    }

    private func select(_ section: TaskControlSection) {
        guard store.section != section else { return }
        store.section = section
        store.select(taskID: nil)
        Task { await store.refresh() }
    }

    private var listHeader: some View {
        HStack(spacing: 12) {
            sectionMenu
            Spacer(minLength: 8)
            if store.isLoading {
                ProgressView().controlSize(.small)
            } else {
                Text("\(store.count(for: store.section))")
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("\(store.count(for: store.section)) missions")
            }
        }
        .padding(14)
        .background(.regularMaterial)
    }

    private var taskList: some View {
        Group {
            if store.visibleTasks.isEmpty {
                EmptyState(
                    symbol: "tray",
                    title: "Rien ici",
                    subtitle: emptyMessage
                )
            } else {
                List(store.visibleTasks, selection: taskSelectionBinding) { task in
                    MissionRow(task: task).tag(task.id)
                }
                .listStyle(.inset)
            }
        }
    }

    private var emptyMessage: String {
        switch store.section {
        case .toApprove: "Aucun plan n'attend votre validation."
        case .attention: "Rien ne réclame votre attention."
        case .running: "Jarvis n'exécute aucune mission actuellement."
        default: "Aucune mission dans cette section."
        }
    }

    private var taskSelectionBinding: Binding<String?> {
        Binding(get: { store.selectedTaskID }, set: { store.select(taskID: $0) })
    }

    private var candidateList: some View {
        Group {
            if store.candidates.isEmpty {
                EmptyState(
                    symbol: "sparkle.magnifyingglass",
                    title: "Aucune suggestion",
                    subtitle: "JARVIS n'a repéré aucune demande à confirmer."
                )
            } else {
                List(store.candidates) { candidate in
                    CandidateRow(candidate: candidate) { decision in
                        Task { await store.decideCandidate(candidate, decision: decision) }
                    }
                }
                .listStyle(.inset)
            }
        }
    }

    // MARK: - Détail

    @ViewBuilder
    private var detailColumn: some View {
        if let task = store.selectedTask {
            TaskDetailView(
                task: task,
                store: store,
                onRequestCancel: { isCancelConfirmPresented = true }
            )
        } else {
            missionPlaceholder
        }
    }

    private var missionPlaceholder: some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 7) {
                Image(systemName: "gearshape.2.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(JarvisPalette.cyan)
                Text("Pilotez le travail confié à Jarvis")
                    .font(.title2.weight(.semibold))
                Text("Sélectionnez une mission pour vérifier son plan, suivre son exécution et récupérer son résultat.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(spacing: 10) {
                workflowStep(1, "Jarvis prépare", "Un plan lisible, sans démarrer le travail.", "doc.text.magnifyingglass")
                workflowStep(2, "Vous décidez", "Validez, demandez une modification ou refusez.", "checkmark.shield")
                workflowStep(3, "Jarvis exécute", "Suivez l'activité et récupérez les livrables.", "shippingbox.fill")
            }
        }
        .padding(28)
        .frame(maxWidth: 620, maxHeight: .infinity, alignment: .center)
        .frame(maxWidth: .infinity)
    }

    private func workflowStep(
        _ number: Int, _ title: String, _ subtitle: String, _ symbol: String
    ) -> some View {
        HStack(spacing: 14) {
            Text("\(number)")
                .font(.caption.monospacedDigit().weight(.bold))
                .frame(width: 26, height: 26)
                .background(JarvisPalette.blue.opacity(0.22), in: Circle())
            Image(systemName: symbol)
                .foregroundStyle(JarvisPalette.cyan)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.semibold))
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(12)
        .jarvisGlass(cornerRadius: 14)
    }

    // MARK: - Barre d'outils

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if let message = store.statusMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .transition(.opacity)
            }
            if store.attentionCount > 0 {
                Button {
                    store.section = .attention
                    Task { await store.refresh() }
                } label: {
                    Label("\(store.attentionCount)", systemImage: "bell.badge.fill")
                }
                .help("Afficher les missions demandant votre attention (⌥⌘A)")
                .keyboardShortcut("a", modifiers: [.option, .command])
            }
            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .disabled(store.isLoading)
            .help("Actualiser")

            Button {
                isCreating = true
            } label: {
                Label("Nouvelle mission", systemImage: "plus")
            }
            .keyboardShortcut("n", modifiers: .command)
            .help("Nouvelle mission Jarvis (⌘N)")
        }
    }
}

// MARK: - Ligne de tâche

private struct MissionRow: View {
    let task: ControlTask

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            RoundedRectangle(cornerRadius: 2)
                .fill(task.status.tint)
                .frame(width: 3)
            VStack(alignment: .leading, spacing: 4) {
                Text(task.title)
                    .font(.body.weight(.medium))
                    .lineLimit(2)
                if !task.description.isEmpty {
                    Text(task.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                HStack(spacing: 8) {
                    Label(task.status.label, systemImage: task.status.symbol)
                        .font(.caption)
                        .foregroundStyle(task.status.tint)
                    if !task.currentPhase.isEmpty, task.status.isExecuting {
                        Text(task.currentPhase)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    if task.priority == "high" {
                        Text("Prioritaire")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.orange)
                    }
                }
                Label(task.source.sourceType.label, systemImage: task.source.sourceType.symbol)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                if task.status.isExecuting, task.progress > 0 {
                    ProgressView(value: task.progress)
                        .progressViewStyle(.linear)
                        .frame(maxWidth: 180)
                        .accessibilityLabel("Progression")
                        .accessibilityValue("\(Int(task.progress * 100)) %")
                }
            }
            Spacer(minLength: 4)
            if task.attentionRequired {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundStyle(.orange)
                    .accessibilityLabel("Attention requise")
            }
        }
        .padding(.vertical, 7)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Ligne de candidat

private struct CandidateRow: View {
    let candidate: TaskCandidate
    let onDecision: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(candidate.suggestedTitle).font(.body.weight(.medium))
            HStack(spacing: 8) {
                Label(candidate.source.sourceType.label, systemImage: candidate.source.sourceType.symbol)
                Text("confiance \(candidate.confidenceLabel)")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            if !candidate.reason.isEmpty {
                Text("Repérée par : \(candidate.reason)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if let duplicate = candidate.duplicateOf {
                Text("Une mission ouverte existe déjà pour cette source (\(duplicate)).")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            HStack(spacing: 8) {
                Button("Créer la mission") { onDecision("accepted") }
                    .buttonStyle(.borderedProminent)
                Button("Ignorer") { onDecision("ignored") }
                Button("Faux positif") { onDecision("false_positive") }
            }
            .controlSize(.small)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Feuille de création

private struct NewMissionSheet: View {
    @Environment(\.dismiss) private var dismiss

    @State private var title = ""
    @State private var description = ""
    @State private var priority = "medium"
    @State private var hasDueDate = false
    @State private var dueDate = Date().addingTimeInterval(86_400)
    @State private var project = ""
    @State private var comment = ""

    let onCreate: (String, String, String, Date?, String?, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section {
                    Label("Nouvelle mission Jarvis", systemImage: "gearshape.2.fill")
                        .font(.title2.weight(.semibold))
                    Text("Décrivez le résultat attendu. Jarvis préparera un plan avant toute exécution.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Section {
                    TextField("Résultat attendu", text: $title)
                    TextField("Contexte et contraintes", text: $description, axis: .vertical)
                        .lineLimit(3...8)
                }
                Section {
                    Picker("Priorité", selection: $priority) {
                        Text("Secondaire").tag("low")
                        Text("Normale").tag("medium")
                        Text("Prioritaire").tag("high")
                    }
                    TextField("Projet (optionnel)", text: $project)
                    Toggle("Échéance", isOn: $hasDueDate)
                    if hasDueDate {
                        DatePicker("Pour le", selection: $dueDate)
                    }
                }
                Section("Contexte initial") {
                    TextField("Commentaire transmis au plan", text: $comment, axis: .vertical)
                        .lineLimit(2...6)
                }
            }
            .formStyle(.grouped)

            Divider()
            HStack {
                Label(
                    "La mission ne démarrera pas : son plan sera d'abord soumis à votre validation.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Spacer()
                Button("Annuler", role: .cancel) { dismiss() }
                Button("Préparer le plan") {
                    onCreate(
                        title.trimmingCharacters(in: .whitespacesAndNewlines),
                        description,
                        priority,
                        hasDueDate ? dueDate : nil,
                        project.isEmpty ? nil : project,
                        comment
                    )
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(16)
        }
        .frame(width: 560, height: 520)
    }
}
