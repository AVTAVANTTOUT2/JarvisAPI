import Foundation

enum JarvisAPIError: LocalizedError {
    case invalidURL
    case transport(String)
    case http(Int, String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL: "Adresse du cœur Jarvis invalide."
        case .transport(let message): message
        case .http(let status, let message): message.isEmpty ? "Erreur HTTP \(status)" : message
        case .decoding(let message): "Réponse Jarvis illisible : \(message)"
        }
    }
}

@MainActor
final class JarvisAPI {
    static let defaultBaseURL = "https://127.0.0.1:8081"

    private(set) var csrfToken: String?
    private let trustDelegate: LocalTrustDelegate
    private let session: URLSession

    var baseURLString: String {
        let stored = UserDefaults.standard.string(forKey: "jarvis.baseURL")?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (stored?.isEmpty == false ? stored! : Self.defaultBaseURL)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    var baseURL: URL? { URL(string: baseURLString) }

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 45
        configuration.httpCookieStorage = .shared
        configuration.httpShouldSetCookies = true
        configuration.httpAdditionalHeaders = ["Accept-Language": "fr-FR,fr;q=0.9"]
        trustDelegate = LocalTrustDelegate()
        session = URLSession(configuration: configuration, delegate: trustDelegate, delegateQueue: nil)
    }

    func setBaseURL(_ value: String) {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        UserDefaults.standard.set(normalized, forKey: "jarvis.baseURL")
        csrfToken = nil
    }

    func authStatus() async throws -> AuthStatus {
        let status: AuthStatus = try await request("/api/auth/status")
        csrfToken = status.csrfToken
        return status
    }

    func discoverAuthStatus() async throws -> AuthStatus {
        let original = baseURLString
        let candidates = [
            original,
            "https://127.0.0.1:8081",
            "https://127.0.0.1:9000",
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8081",
            "http://127.0.0.1:9000",
        ]
        var lastError: Error = JarvisAPIError.transport("Aucun cœur Jarvis détecté.")
        let uniqueCandidates = NSOrderedSet(array: candidates).array.compactMap { $0 as? String }
        for candidate in uniqueCandidates {
            setBaseURL(candidate)
            do { return try await authStatus() }
            catch { lastError = error }
        }
        setBaseURL(original)
        throw lastError
    }

    func unlock(secret: String, setup: Bool = false) async throws {
        let path = setup ? "/api/auth/setup" : "/api/auth/unlock"
        let response: AuthMutationResponse = try await request(
            path,
            method: "POST",
            body: ["secret": secret],
            requiresCSRF: false
        )
        guard response.ok else { throw JarvisAPIError.transport("Déverrouillage refusé.") }
        csrfToken = response.csrfToken
    }

    func logout() async throws {
        let _: OKResponse = try await request(
            "/api/auth/logout",
            method: "POST",
            body: [String: String]()
        )
        csrfToken = nil
    }

    func tasks(status: String? = nil) async throws -> [JarvisTask] {
        let suffix = status.map { "?status=\($0)" } ?? ""
        let envelope: TaskEnvelope = try await request("/api/tasks\(suffix)")
        return envelope.tasks
    }

    func createTask(title: String, priority: String = "medium") async throws -> JarvisTask {
        let envelope: TaskMutationEnvelope = try await request(
            "/api/tasks",
            method: "POST",
            body: ["title": title, "priority": priority]
        )
        return envelope.task
    }

    func updateTask(_ task: JarvisTask, status: String) async throws -> JarvisTask {
        let envelope: TaskMutationEnvelope = try await request(
            "/api/tasks/\(task.id)",
            method: "PATCH",
            body: ["status": status]
        )
        return envelope.task
    }

    func notifications() async throws -> [JarvisNotification] {
        let envelope: NotificationEnvelope = try await request("/api/notifications")
        return envelope.notifications
    }

    func markNotificationRead(_ id: Int) async throws {
        let _: OKResponse = try await request(
            "/api/notifications/\(id)/read",
            method: "POST",
            body: [String: String]()
        )
    }

    func calendarToday() async throws -> [CalendarItem] {
        let calendar = Calendar.current
        let start = calendar.startOfDay(for: .now)
        let end = calendar.date(byAdding: .day, value: 2, to: start) ?? .now
        let formatter = ISO8601DateFormatter()
        let startString = formatter.string(from: start)
        let endString = formatter.string(from: end)
        var components = URLComponents()
        components.path = "/api/calendar"
        components.queryItems = [
            URLQueryItem(name: "start", value: startString),
            URLQueryItem(name: "end", value: endString),
        ]
        let envelope: CalendarEnvelope = try await request(components.string ?? "/api/calendar")
        return envelope.events
    }

    func status() async throws -> StatusResponse { try await request("/api/status") }

    func integrations() async throws -> IntegrationsResponse {
        try await request("/api/integrations")
    }

    func conversations() async throws -> [ConversationSummary] {
        let envelope: ConversationEnvelope = try await request("/api/conversations?limit=40")
        return envelope.conversations
    }

    func conversation(id: Int) async throws -> ConversationDetail {
        try await request("/api/conversations/\(id)")
    }

    func briefing(kind: String = "morning") async throws -> BriefingResponse {
        try await request("/api/briefing?kind=\(kind)")
    }

    func websocketRequest() throws -> URLRequest {
        guard let baseURL else { throw JarvisAPIError.invalidURL }
        let scheme = baseURL.scheme == "https" ? "wss" : "ws"
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        components?.scheme = scheme
        components?.path = "/ws"
        components?.query = nil
        guard let url = components?.url else { throw JarvisAPIError.invalidURL }
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        request.setValue(baseURLString, forHTTPHeaderField: "Origin")
        if let cookies = HTTPCookieStorage.shared.cookies(for: baseURL), !cookies.isEmpty {
            let headers = HTTPCookie.requestHeaderFields(with: cookies)
            request.setValue(headers["Cookie"], forHTTPHeaderField: "Cookie")
        }
        return request
    }

    private func request<Response: Decodable, Body: Encodable>(
        _ path: String,
        method: String = "GET",
        body: Body?,
        requiresCSRF: Bool = true
    ) async throws -> Response {
        guard let baseURL, let url = URL(string: path, relativeTo: baseURL) else {
            throw JarvisAPIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = method == "GET" ? 20 : 60
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(baseURLString, forHTTPHeaderField: "Origin")
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if method != "GET", requiresCSRF, let csrfToken {
            request.setValue(csrfToken, forHTTPHeaderField: "X-CSRF-Token")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw JarvisAPIError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw JarvisAPIError.transport("Réponse réseau invalide.")
        }
        guard 200..<300 ~= http.statusCode else {
            let message = Self.extractError(from: data)
            throw JarvisAPIError.http(http.statusCode, message)
        }
        do {
            return try JSONDecoder().decode(Response.self, from: data)
        } catch {
            throw JarvisAPIError.decoding(error.localizedDescription)
        }
    }

    private func request<Response: Decodable>(
        _ path: String,
        method: String = "GET",
        requiresCSRF: Bool = true
    ) async throws -> Response {
        try await request(
            path,
            method: method,
            body: Optional<String>.none,
            requiresCSRF: requiresCSRF
        )
    }

    private static func extractError(from data: Data) -> String {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return String(data: data, encoding: .utf8) ?? "" }
        return (object["detail"] as? String)
            ?? (object["error"] as? String)
            ?? ""
    }
}

final class LocalTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        let host = challenge.protectionSpace.host.lowercased()
        let isLoopback = host == "localhost" || host == "127.0.0.1" || host == "::1"
        if
            isLoopback,
            challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust
        {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
