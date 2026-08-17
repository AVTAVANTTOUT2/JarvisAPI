import AppKit
import SwiftUI

/// Détail d'une mission pilotée.
///
/// L'onglet Activité s'inspire des outils de développement agentiques : on y
/// voit l'agent actif, l'étape, l'outil, le fichier, le test. On n'y voit
/// jamais un raisonnement brut — le serveur ne l'émet pas, et l'écran ne
/// dispose d'aucun champ où l'afficher.
struct TaskDetailView: View {
    let task: ControlTask
    @ObservedObject var store: TaskControlStore
    let onRequestCancel: () -> Void

    enum Tab: String, CaseIterable, Identifiable {
        case summary, plan, activity, permissions, result, context
        var id: String { rawValue }
        var title: String {
            switch self {
            case .summary: "Résumé"
            case .plan: "Plan"
            case .activity: "Activité"
            case .permissions: "Autorisations"
            case .result: "Résultat"
            case .context: "Contexte"
            }
        }
    }

    @State private var tab: Tab = .summary
    @State private var decisionComment = ""
    @State private var newComment = ""
    @State private var requestRevisionWithComment = false
    @State private var activityLevel = "detail"

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            Picker("Vue", selection: $tab) {
                ForEach(Tab.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            Divider()
            ScrollView { content.padding(20) }
        }
        .onChange(of: task.id) { _, _ in
            tab = .summary
            decisionComment = ""
            newComment = ""
        }
    }

    // MARK: - En-tête

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(task.title).font(.title2.weight(.semibold))
                Spacer()
                Label(task.status.label, systemImage: task.status.symbol)
                    .font(.callout.weight(.medium))
                    .foregroundStyle(task.status.tint)
            }
            HStack(spacing: 14) {
                Label(task.source.sourceType.label, systemImage: task.source.sourceType.symbol)
                Text(task.priorityLabel)
                if !task.currentPhase.isEmpty { Text("Phase : \(task.currentPhase)") }
                if let version = task.approvedPlanVersion {
                    Text("Plan approuvé v\(version)")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if task.status.isExecuting {
                ProgressView(value: task.progress)
                    .progressViewStyle(.linear)
                    .accessibilityLabel("Progression de la mission")
                    .accessibilityValue("\(Int(task.progress * 100)) %")
            }
            actionBar
        }
        .padding(18)
    }

    private var actionBar: some View {
        HStack(spacing: 10) {
            if task.isDecidable, let plan = store.currentPlan {
                Button {
                    Task { await store.decidePlan("approved", comment: decisionComment) }
                } label: {
                    Label("Accepter et planifier", systemImage: "checkmark.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: .command)
                .help("Accepter le plan v\(plan.version) et lancer l'exécution (⌘↩)")
                .disabled(store.isMutating)

                Button("Demander une modification") {
                    Task {
                        await store.decidePlan(
                            "revision_requested", comment: decisionComment
                        )
                    }
                }
                .disabled(store.isMutating)

                Button("Refuser", role: .destructive) {
                    Task { await store.decidePlan("rejected", comment: decisionComment) }
                }
                .disabled(store.isMutating)
            }
            if task.isCancellable {
                Button("Annuler la mission", role: .destructive, action: onRequestCancel)
                    .keyboardShortcut(".", modifiers: .command)
                    .help("Annuler la mission (⌘.)")
                    .disabled(store.isMutating)
            }
            if store.isMutating { ProgressView().controlSize(.small) }
            Spacer()
        }
    }

    // MARK: - Contenu

    @ViewBuilder
    private var content: some View {
        switch tab {
        case .summary: summaryTab
        case .plan: planTab
        case .activity: activityTab
        case .permissions: permissionsTab
        case .result: resultTab
        case .context: contextTab
        }
    }

    // MARK: Résumé

    private var summaryTab: some View {
        VStack(alignment: .leading, spacing: 16) {
            if !task.description.isEmpty {
                DetailBlock(title: "Description") { Text(task.description) }
            }
            DetailBlock(title: "État") {
                VStack(alignment: .leading, spacing: 6) {
                    keyValue("Statut", task.status.label)
                    keyValue("Priorité", task.priorityLabel)
                    keyValue("Provenance", task.source.sourceType.label)
                    if let due = task.dueAt { keyValue("Échéance", due) }
                    if let created = task.createdAt { keyValue("Créée le", created) }
                    if let result = task.resultStatus { keyValue("Résultat", result) }
                }
            }
            if task.status == .awaitingPlanApproval {
                Label(
                    "Rien n'a été exécuté. L'exécution ne démarrera qu'après votre validation du plan.",
                    systemImage: "lock.shield"
                )
                .font(.callout)
                .foregroundStyle(.orange)
            }
        }
    }

    // MARK: Plan

    @ViewBuilder
    private var planTab: some View {
        if let plan = store.currentPlan {
            VStack(alignment: .leading, spacing: 16) {
                DetailBlock(title: "Objectif") { Text(plan.objective) }
                if !plan.contextUnderstood.isEmpty {
                    DetailBlock(title: "Contexte compris") { Text(plan.contextUnderstood) }
                }
                if !plan.summary.isEmpty {
                    DetailBlock(title: "Résumé") { Text(plan.summary) }
                }
                DetailBlock(title: "Étapes (v\(plan.version))") {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(plan.steps) { step in
                            VStack(alignment: .leading, spacing: 3) {
                                Text("\(step.index). \(step.title)").font(.body.weight(.medium))
                                if !step.detail.isEmpty {
                                    Text(step.detail).font(.callout).foregroundStyle(.secondary)
                                }
                                if !step.expectedResult.isEmpty {
                                    Text("Attendu : \(step.expectedResult)")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                if !step.tools.isEmpty {
                                    Text(step.tools.joined(separator: ", "))
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                }
                if !plan.externalEffectPermissions.isEmpty {
                    DetailBlock(title: "Effets hors de cette machine") {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(
                                "Ce plan annonce des effets externes. Chacun demandera une autorisation séparée au moment venu — accepter le plan ne les autorise pas."
                            )
                            .font(.callout)
                            .foregroundStyle(.orange)
                            Text(plan.externalEffectPermissions.joined(separator: ", "))
                                .font(.caption.monospaced())
                        }
                    }
                }
                bulletBlock("Livrables attendus", plan.expectedDeliverables)
                bulletBlock("Outils nécessaires", plan.toolsExpected)
                bulletBlock("Autorisations accordées au démarrage", plan.executionPermissions ?? [])
                bulletBlock("Autorisations anticipées par le plan", plan.permissionsExpected)
                bulletBlock("Risques", plan.risks)
                bulletBlock("Hypothèses", plan.assumptions)
                bulletBlock("Critères de réussite", plan.successCriteria)
                bulletBlock("Limites connues", plan.knownLimits)
                DetailBlock(title: "Estimation") {
                    keyValue("Durée", plan.estimatedDurationLabel)
                }
                if task.isDecidable {
                    DetailBlock(title: "Commentaire joint à votre décision") {
                        TextField("Optionnel", text: $decisionComment, axis: .vertical)
                            .lineLimit(2...5)
                            .textFieldStyle(.roundedBorder)
                    }
                }
                if let detail = store.detail, detail.plans.count > 1 {
                    DetailBlock(title: "Versions") {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(detail.plans) { version in
                                HStack {
                                    Text("v\(version.version)").font(.callout.monospacedDigit())
                                    Text(version.decision).foregroundStyle(.secondary)
                                    if !version.decisionComment.isEmpty {
                                        Text("— \(version.decisionComment)")
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .font(.caption)
                            }
                        }
                    }
                }
            }
        } else if store.isDetailLoading {
            ProgressView("Chargement du plan…")
        } else {
            EmptyState(
                symbol: "doc.text",
                title: "Aucun plan",
                subtitle: "La planification n'a pas encore produit de version."
            )
        }
    }

    // MARK: Activité

    private var activityTab: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Niveau", selection: $activityLevel) {
                Text("Résumé").tag("summary")
                Text("Détails").tag("detail")
                Text("Technique").tag("technical")
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 320)

            if store.activity.isEmpty {
                EmptyState(
                    symbol: "waveform",
                    title: "Aucune activité",
                    subtitle: "L'activité des agents apparaîtra ici dès le démarrage."
                )
            } else {
                ForEach(filteredActivity) { entry in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: entry.symbol)
                            .foregroundStyle(entry.isProblem ? Color.orange : Color.secondary)
                            .frame(width: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 8) {
                                Text(entry.roleLabel).font(.caption.weight(.semibold))
                                if !entry.toolName.isEmpty {
                                    Text(entry.toolName).font(.caption.monospaced())
                                        .foregroundStyle(.tertiary)
                                }
                                Spacer()
                                if let created = entry.createdAt {
                                    Text(created.suffix(8)).font(.caption2.monospacedDigit())
                                        .foregroundStyle(.tertiary)
                                }
                            }
                            Text(entry.summary).font(.callout)
                            if !entry.artifactReference.isEmpty {
                                Text(entry.artifactReference)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                    .padding(.vertical, 4)
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    private var filteredActivity: [TaskActivityEntry] {
        switch activityLevel {
        case "summary": store.activity.filter { $0.level == "summary" }
        case "detail": store.activity.filter { $0.level != "technical" }
        default: store.activity
        }
    }

    // MARK: Autorisations

    private var permissionsTab: some View {
        VStack(alignment: .leading, spacing: 14) {
            if store.pendingApprovals.isEmpty {
                EmptyState(
                    symbol: "hand.raised",
                    title: "Aucune autorisation en attente",
                    subtitle: "Rien ne réclame votre décision pour l'instant."
                )
            } else {
                ForEach(store.pendingApprovals) { approval in
                    DetailBlock(title: approval.action) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(approval.summary).font(.callout)
                            keyValue("Outil", approval.tool)
                            keyValue("Portée", approval.scope)
                            if let expires = approval.expiresAt {
                                keyValue("Expire", expires)
                            }
                            if !approval.sanitizedArguments.isEmpty {
                                Text("Détail de l'action")
                                    .font(.caption.weight(.semibold))
                                ForEach(approval.sanitizedArguments.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                                    HStack(alignment: .top, spacing: 6) {
                                        Text(key).font(.caption.monospaced()).foregroundStyle(.secondary)
                                        Text(value).font(.caption.monospaced()).textSelection(.enabled)
                                    }
                                }
                            }
                            if !approval.risks.isEmpty {
                                ForEach(approval.risks, id: \.self) { risk in
                                    Label(risk, systemImage: "exclamationmark.triangle")
                                        .font(.caption)
                                        .foregroundStyle(.orange)
                                }
                            }
                            HStack(spacing: 10) {
                                Button("Autoriser cette action") {
                                    Task { await store.decideApproval(approval, approved: true) }
                                }
                                .buttonStyle(.borderedProminent)
                                Button("Refuser", role: .destructive) {
                                    Task { await store.decideApproval(approval, approved: false) }
                                }
                            }
                            .disabled(store.isMutating)
                            Text(
                                "Cette autorisation ne vaut que pour cette action, avec ces arguments, une seule fois."
                            )
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    // MARK: Résultat

    @ViewBuilder
    private var resultTab: some View {
        if let report = store.report {
            VStack(alignment: .leading, spacing: 16) {
                DetailBlock(title: "Résumé exécutif") {
                    Text(report.summary).font(.callout)
                }
                if !report.deliveries.isEmpty {
                    DetailBlock(title: "Lieu de livraison") {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(report.deliveries) { delivery in
                                HStack(spacing: 8) {
                                    Text(delivery.label).font(.caption.weight(.semibold))
                                    Text(delivery.reference)
                                        .font(.caption.monospaced())
                                        .textSelection(.enabled)
                                    Spacer()
                                    deliveryActions(delivery)
                                }
                            }
                        }
                    }
                }
                DetailBlock(title: "Rapport complet") {
                    // Le rapport est du Markdown produit par JARVIS ; le rendu
                    // natif conserve titres et listes sans exécuter de HTML.
                    Text(markdown(report.markdown))
                        .textSelection(.enabled)
                }
            }
        } else if task.status.isExecuting {
            EmptyState(
                symbol: "hourglass",
                title: "Tâche en cours",
                subtitle: "Le rapport sera disponible à la conclusion."
            )
        } else {
            EmptyState(
                symbol: "doc.richtext",
                title: "Aucun rapport",
                subtitle: "Aucune conclusion n'a encore été produite."
            )
        }
    }

    @ViewBuilder
    private func deliveryActions(_ delivery: TaskDelivery) -> some View {
        HStack(spacing: 6) {
            if let url = delivery.openableURL {
                Button("Ouvrir") { NSWorkspace.shared.open(url) }
            } else if let path = delivery.localPath {
                Button("Révéler") {
                    NSWorkspace.shared.selectFile(
                        path, inFileViewerRootedAtPath: (path as NSString).deletingLastPathComponent
                    )
                }
                .help("Afficher dans le Finder")
            }
            Button("Copier") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(delivery.reference, forType: .string)
            }
        }
        .controlSize(.small)
    }

    private func markdown(_ raw: String) -> AttributedString {
        (try? AttributedString(
            markdown: raw,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(raw)
    }

    // MARK: Contexte

    private var contextTab: some View {
        VStack(alignment: .leading, spacing: 16) {
            DetailBlock(title: "Provenance") {
                VStack(alignment: .leading, spacing: 6) {
                    keyValue("Type", task.source.sourceType.label)
                    keyValue("Canal", task.source.channel)
                    if !task.source.sender.isEmpty { keyValue("Expéditeur", task.source.sender) }
                    if !task.source.subject.isEmpty { keyValue("Sujet", task.source.subject) }
                    if !task.source.reference.isEmpty {
                        keyValue("Référence", task.source.reference)
                    }
                    if let confidence = task.source.confidence {
                        keyValue("Confiance", "\(Int((confidence * 100).rounded())) %")
                    }
                    if !task.source.detectionReason.isEmpty {
                        keyValue("Repérée par", task.source.detectionReason)
                    }
                }
            }
            if !task.source.excerpt.isEmpty {
                DetailBlock(title: "Extrait de la source") {
                    Text(task.source.excerpt)
                        .font(.callout)
                        .textSelection(.enabled)
                }
            }
            DetailBlock(title: "Ajouter une précision") {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("Commentaire", text: $newComment, axis: .vertical)
                        .lineLimit(2...6)
                        .textFieldStyle(.roundedBorder)
                    Toggle(
                        "Cette précision change le périmètre — demander un nouveau plan",
                        isOn: $requestRevisionWithComment
                    )
                    .font(.caption)
                    Button("Enregistrer") {
                        let body = newComment
                        let revise = requestRevisionWithComment
                        newComment = ""
                        requestRevisionWithComment = false
                        Task { await store.addComment(body, requestPlanRevision: revise) }
                    }
                    .disabled(
                        store.isMutating
                            || newComment.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
            }
            if let comments = store.detail?.comments, !comments.isEmpty {
                DetailBlock(title: "Historique des précisions") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(comments) { comment in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(comment.body).font(.callout)
                                Text(comment.createdAt ?? "")
                                    .font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Petits composants

    @ViewBuilder
    private func bulletBlock(_ title: String, _ values: [String]) -> some View {
        if !values.isEmpty {
            DetailBlock(title: title) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(values, id: \.self) { value in
                        Label(value, systemImage: "circle.fill")
                            .labelStyle(BulletLabelStyle())
                    }
                }
            }
        }
    }

    private func keyValue(_ key: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(key).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                .frame(width: 110, alignment: .leading)
            Text(value).font(.callout).textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }
}

private struct DetailBlock<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.weight(.bold))
                .tracking(0.6)
                .foregroundStyle(.secondary)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .jarvisGlass(cornerRadius: 14)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}

private struct BulletLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            configuration.icon.font(.system(size: 4)).foregroundStyle(.tertiary)
            configuration.title.font(.callout)
        }
    }
}
