import AppKit
import SwiftUI

struct ActionsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var newTask = ""
    @State private var priority = "medium"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                SectionHeader(
                    eyebrow: "EXÉCUTION",
                    title: "Actions",
                    subtitle: "Décider vite, laisser Jarvis suivre le reste."
                )
                quickAdd
                HStack(alignment: .top, spacing: 16) {
                    taskList
                    agenda
                }
            }
            .padding(28)
        }
        .navigationTitle("Actions")
    }

    private var quickAdd: some View {
        HStack(spacing: 12) {
            Image(systemName: "plus.circle.fill").foregroundStyle(JarvisPalette.cyan).font(.title2)
            TextField("Ajouter une action…", text: $newTask)
                .textFieldStyle(.plain)
                .font(.title3)
                .onSubmit { addTask() }
            Picker("Priorité", selection: $priority) {
                Text("Secondaire").tag("low")
                Text("Normale").tag("medium")
                Text("Prioritaire").tag("high")
            }
            .labelsHidden()
            .frame(width: 130)
            Button("Ajouter") { addTask() }
                .buttonStyle(.borderedProminent)
                .disabled(newTask.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(16)
        .jarvisGlass(cornerRadius: 18)
    }

    private var taskList: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack {
                Label("À faire", systemImage: "checklist").font(.headline)
                Spacer()
                Text("\(model.snapshot.tasks.count)").font(.caption).foregroundStyle(.secondary)
            }
            if model.snapshot.tasks.isEmpty {
                EmptyState(symbol: "checkmark.seal.fill", title: "Liste vide", subtitle: "Ajoutez une action ou demandez à Jarvis de le faire.")
                    .frame(height: 280)
            } else {
                ForEach(model.snapshot.tasks) { task in
                    TaskRow(task: task) { Task { await model.toggleTask(task) } }
                    if task.id != model.snapshot.tasks.last?.id { Divider().opacity(0.45) }
                }
            }
        }
        .jarvisCardPadding()
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .jarvisGlass()
    }

    private var agenda: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Prochainement", systemImage: "calendar").font(.headline)
            if model.snapshot.calendar.isEmpty {
                EmptyState(symbol: "calendar.badge.checkmark", title: "Rien de prévu", subtitle: "Calendar ne remonte aucun événement proche.")
                    .frame(height: 220)
            } else {
                ForEach(model.snapshot.calendar) { item in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(item.title).font(.subheadline.weight(.semibold))
                        HStack {
                            Label(formatDate(item.start), systemImage: "clock")
                            if let location = item.location, !location.isEmpty {
                                Label(location, systemImage: "location")
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 5)
                }
            }
            Divider().opacity(0.5)
            Button("Ouvrir Calendar") {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Calendar.app"))
            }
            .buttonStyle(JarvisSecondaryButtonStyle())
        }
        .jarvisCardPadding()
        .frame(width: 330, alignment: .topLeading)
        .jarvisGlass()
    }

    private func addTask() {
        let title = newTask
        Task {
            if await model.createTask(title: title, priority: priority) { newTask = "" }
        }
    }

    private func formatDate(_ value: String) -> String {
        guard let date = value.jarvisDate else { return value }
        return date.formatted(.dateTime.weekday(.abbreviated).hour().minute())
    }
}

private struct TaskRow: View {
    let task: JarvisTask
    let toggle: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Button(action: toggle) {
                Image(systemName: task.isDone ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(task.isDone ? .green : .secondary)
            }
            .buttonStyle(.plain)
            VStack(alignment: .leading, spacing: 5) {
                Text(task.title).font(.body.weight(.medium)).strikethrough(task.isDone)
                HStack(spacing: 8) {
                    StatusPill(text: task.priorityLabel, color: task.priority == "high" ? .orange : .blue)
                    if let category = task.category, !category.isEmpty {
                        Text(category).font(.caption).foregroundStyle(.secondary)
                    }
                    if let due = task.dueDate, !due.isEmpty {
                        Label(due, systemImage: "clock").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }
}
