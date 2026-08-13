import AppKit
import SwiftUI

/// Section « Tâches » — pilotage du moteur agentique.
///
/// Trois colonnes natives : sections, liste, détail. La colonne de détail
/// porte l'essentiel du parcours produit (plan, activité, autorisations,
/// résultat) et vit dans `TaskDetailView`.
struct TasksView: View {
    @StateObject private var store: TaskControlStore

    @State private var isCreating = false
    @State private var isCancelConfirmPresented = false

    init(api: JarvisAPI) {
        _store = StateObject(wrappedValue: TaskControlStore(api: api))
    }

    var body: some View {
        NavigationSplitView {
            sectionList
                .navigationSplitViewColumnWidth(min: 210, ideal: 230, max: 280)
        } content: {
            taskColumn
                .navigationSplitViewColumnWidth(min: 300, ideal: 360, max: 460)
        } detail: {
            detailColumn
        }
        .navigationTitle("Tâches")
        .toolbar { toolbarItems }
        .sheet(isPresented: $isCreating) {
            NewTaskSheet { title, description, priority, dueAt, project, comment in
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
            "Annuler cette tâche ?",
            isPresented: $isCancelConfirmPresented,
            titleVisibility: .visible
        ) {
            Button("Annuler la tâche", role: .destructive) {
                Task { await store.cancelTask(reason: "Annulée depuis macOS") }
            }
            Button("Continuer", role: .cancel) {}
        } message: {
            Text("L'exécution en cours sera interrompue. Le travail déjà produit reste consultable.")
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

    // MARK: - Colonne 1 : sections

    private var sectionList: some View {
        List(TaskControlSection.allCases, selection: sectionBinding) { section in
            HStack {
                Label(section.title, systemImage: section.symbol)
                Spacer()
                let count = store.count(for: section)
                if count > 0 {
                    Text("\(count)")
                        .font(.caption.monospacedDigit())
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(
                            Capsule().fill(
                                section == .attention
                                    ? Color.orange.opacity(0.25)
                                    : Color.secondary.opacity(0.15)
                            )
                        )
                        .accessibilityLabel("\(count) tâches")
                }
            }
            .tag(section)
            .padding(.vertical, 3)
        }
        .listStyle(.sidebar)
        .accessibilityLabel("Sections des tâches")
    }

    private var sectionBinding: Binding<TaskControlSection?> {
        Binding(
            get: { store.section },
            set: { newValue in
                guard let newValue else { return }
                store.section = newValue
                store.select(taskID: nil)
                Task { await store.refresh() }
            }
        )
    }

    // MARK: - Colonne 2 : liste

    @ViewBuilder
    private var taskColumn: some View {
        VStack(spacing: 0) {
            if store.section.isCandidateSection {
                candidateList
            } else {
                taskList
            }
        }
        .searchable(text: $store.searchText, prompt: "Filtrer les tâches")
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
                    TaskRow(task: task).tag(task.id)
                }
                .listStyle(.inset)
            }
        }
    }

    private var emptyMessage: String {
        switch store.section {
        case .toApprove: "Aucun plan n'attend votre validation."
        case .attention: "Rien ne réclame votre attention."
        case .running: "Aucune tâche en cours d'exécution."
        default: "Aucune tâche dans cette section."
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

    // MARK: - Colonne 3 : détail

    @ViewBuilder
    private var detailColumn: some View {
        if let task = store.selectedTask {
            TaskDetailView(
                task: task,
                store: store,
                onRequestCancel: { isCancelConfirmPresented = true }
            )
        } else {
            EmptyState(
                symbol: "sidebar.right",
                title: "Sélectionnez une tâche",
                subtitle: "Le plan, l'activité et le résultat s'affichent ici."
            )
        }
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
                .help("Afficher les tâches demandant votre attention (⌥⌘A)")
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
                Label("Nouvelle tâche", systemImage: "plus")
            }
            .keyboardShortcut("n", modifiers: .command)
            .help("Nouvelle tâche (⌘N)")
        }
    }
}

// MARK: - Ligne de tâche

private struct TaskRow: View {
    let task: ControlTask

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: task.source.sourceType.symbol)
                .foregroundStyle(.secondary)
                .frame(width: 18)
                .accessibilityLabel(task.source.sourceType.label)
            VStack(alignment: .leading, spacing: 4) {
                Text(task.title)
                    .font(.body.weight(.medium))
                    .lineLimit(2)
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
        .padding(.vertical, 5)
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
                Text("Une tâche ouverte existe déjà pour cette source (\(duplicate)).")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            HStack(spacing: 8) {
                Button("Créer la tâche") { onDecision("accepted") }
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

private struct NewTaskSheet: View {
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
                    TextField("Titre", text: $title)
                    TextField("Description", text: $description, axis: .vertical)
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
                    "La tâche ne démarrera pas : un plan sera préparé, puis soumis à votre validation.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Spacer()
                Button("Annuler", role: .cancel) { dismiss() }
                Button("Créer") {
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
