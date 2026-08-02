import SwiftUI

struct LockView: View {
    @EnvironmentObject private var model: AppModel
    @State private var secret = ""
    @State private var isWorking = false
    @State private var launcherMessage: String?

    var body: some View {
        VStack(spacing: 24) {
            JarvisOrb(size: 88, active: orbIsActive)
            VStack(spacing: 7) {
                Text(title).font(.largeTitle.weight(.semibold)).tracking(-0.7)
                Text(subtitle)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 430)
            }

            switch model.phase {
            case .locked, .setupRequired:
                VStack(spacing: 12) {
                    SecureField(model.phase == .setupRequired ? "Créer un PIN ou une passphrase" : "PIN ou passphrase", text: $secret)
                        .textFieldStyle(.plain)
                        .padding(13)
                        .frame(width: 330)
                        .jarvisGlass(cornerRadius: 14)
                        .onSubmit { submit() }
                    Button(model.phase == .setupRequired ? "Configurer Jarvis" : "Déverrouiller") { submit() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(secret.isEmpty || isWorking)
                }
            case .offline:
                HStack(spacing: 10) {
                    Button("Démarrer le cœur") { startCore() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                    Button("Réessayer") { Task { await model.retryConnection() } }
                        .buttonStyle(JarvisSecondaryButtonStyle())
                }
                if let launcherMessage {
                    Text(launcherMessage).font(.caption).foregroundStyle(.secondary)
                }
            case .checking:
                ProgressView().controlSize(.large)
            case .ready:
                EmptyView()
            }

            Text("Le cerveau, la mémoire et les intégrations restent dans votre cœur Jarvis local.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(48)
        .frame(maxWidth: 590)
        .jarvisGlass(cornerRadius: 30)
        .padding(40)
    }

    private var title: String {
        switch model.phase {
        case .checking: "Réveil de Jarvis"
        case .offline: "Le cœur ne répond pas"
        case .setupRequired: "Bienvenue dans Jarvis"
        case .locked: "Jarvis est verrouillé"
        case .ready: "Jarvis"
        }
    }

    private var orbIsActive: Bool {
        if case .offline = model.phase { return false }
        return true
    }

    private var subtitle: String {
        switch model.phase {
        case .checking: "Connexion sécurisée au cœur local…"
        case .offline(let reason): reason
        case .setupRequired: "Protégez l’accès à votre intelligence personnelle. Ce secret sera aussi utilisé sur le web."
        case .locked: "Utilisez le même secret que sur le dashboard web."
        case .ready: "Opérationnel"
        }
    }

    private func submit() {
        guard !secret.isEmpty else { return }
        isWorking = true
        Task {
            let ok = await model.unlock(secret: secret)
            if ok { secret = "" }
            isWorking = false
        }
    }

    private func startCore() {
        do {
            try JarvisCoreLauncher.start()
            launcherMessage = "Démarrage demandé…"
            Task {
                try? await Task.sleep(for: .seconds(3))
                await model.retryConnection()
            }
        } catch {
            launcherMessage = error.localizedDescription
        }
    }
}
