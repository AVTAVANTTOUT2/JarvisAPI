import Foundation
import LocalAuthentication
import Security

enum BiometricCredentialError: LocalizedError {
    case unavailable
    case keychain(OSStatus)
    case invalidCredential

    var errorDescription: String? {
        switch self {
        case .unavailable:
            "Touch ID n’est pas disponible ou aucune empreinte n’est enregistrée."
        case .keychain(let status):
            "Le trousseau sécurisé a refusé l’accès (code \(status))."
        case .invalidCredential:
            "Le secret biométrique enregistré est invalide."
        }
    }
}

/// Conserve le secret JARVIS dans le trousseau local, lié au jeu biométrique
/// courant. Une empreinte ajoutée ou supprimée invalide automatiquement l’item.
struct BiometricCredentialStore {
    private let service = "com.jarvis.desktop.authentication"
    private let account = "jarvis-core-secret"
    private let availabilityKey = "jarvis.biometricCredentialStored"

    var isAvailable: Bool {
        guard UserDefaults.standard.bool(forKey: availabilityKey) else { return false }
        let context = LAContext()
        var error: NSError?
        return context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &error
        )
    }

    var label: String {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &error
        ) else { return "la biométrie" }
        return switch context.biometryType {
        case .touchID: "Touch ID"
        case .faceID: "Face ID"
        case .opticID: "Optic ID"
        default: "la biométrie"
        }
    }

    func save(secret: String) throws {
        guard !secret.isEmpty else { throw BiometricCredentialError.invalidCredential }
        let context = LAContext()
        var policyError: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &policyError
        ) else {
            delete()
            return
        }
        guard let access = SecAccessControlCreateWithFlags(
            nil,
            kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            .biometryCurrentSet,
            nil
        ) else {
            throw BiometricCredentialError.unavailable
        }

        delete()
        let status = SecItemAdd([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrAccessControl: access,
            kSecAttrSynchronizable: false,
            kSecUseDataProtectionKeychain: true,
            kSecValueData: Data(secret.utf8),
        ] as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw BiometricCredentialError.keychain(status)
        }
        UserDefaults.standard.set(true, forKey: availabilityKey)
    }

    func retrieve() async throws -> String {
        let context = LAContext()
        context.localizedCancelTitle = "Utiliser le secret"
        var policyError: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &policyError
        ) else {
            throw policyError ?? BiometricCredentialError.unavailable
        }

        try await context.evaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            localizedReason: "Déverrouiller votre intelligence personnelle JARVIS"
        )

        var item: CFTypeRef?
        let status = SecItemCopyMatching([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrSynchronizable: false,
            kSecUseDataProtectionKeychain: true,
            kSecUseAuthenticationContext: context,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ] as CFDictionary, &item)
        guard status == errSecSuccess else {
            throw BiometricCredentialError.keychain(status)
        }
        guard let data = item as? Data,
              let secret = String(data: data, encoding: .utf8),
              !secret.isEmpty
        else {
            throw BiometricCredentialError.invalidCredential
        }
        return secret
    }

    func delete() {
        SecItemDelete([
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrSynchronizable: false,
            kSecUseDataProtectionKeychain: true,
        ] as CFDictionary)
        UserDefaults.standard.set(false, forKey: availabilityKey)
    }
}
