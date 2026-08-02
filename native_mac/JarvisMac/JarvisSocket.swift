import Foundation

@MainActor
final class JarvisSocket: ObservableObject {
    @Published private(set) var isConnected = false
    @Published private(set) var lastError: String?

    var onEvent: (([String: Any]) -> Void)?
    var onAudio: ((Data) -> Void)?

    private var socket: URLSessionWebSocketTask?
    private var receiveLoop: Task<Void, Never>?
    private var shouldReconnect = false
    private var lastRequest: URLRequest?
    private let trustDelegate = LocalTrustDelegate()
    private lazy var session = URLSession(configuration: .default, delegate: trustDelegate, delegateQueue: nil)

    func connect(request: URLRequest) {
        disconnect(reconnect: true)
        shouldReconnect = true
        lastRequest = request
        lastError = nil
        let socket = session.webSocketTask(with: request)
        self.socket = socket
        socket.resume()
        receiveLoop = Task { [weak self] in
            await self?.receiveNext(from: socket)
        }
    }

    func disconnect(reconnect: Bool = false) {
        shouldReconnect = reconnect
        receiveLoop?.cancel()
        receiveLoop = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        isConnected = false
    }

    func sendText(_ content: String, stream: Bool = true, tts: Bool = false) {
        sendJSON(["type": "text", "content": content, "stream": stream, "tts": tts])
    }

    func switchConversation(_ id: Int) {
        sendJSON(["type": "switch_conversation", "conversation_id": id])
    }

    func newConversation() {
        sendJSON(["type": "new_conversation"])
    }

    func sendAudio(_ data: Data) {
        guard let socket else { return }
        Task {
            do { try await socket.send(.data(data)) }
            catch { await failed(error, socket: socket) }
        }
    }

    func sendJSON(_ object: [String: Any]) {
        guard
            let socket,
            JSONSerialization.isValidJSONObject(object),
            let data = try? JSONSerialization.data(withJSONObject: object),
            let string = String(data: data, encoding: .utf8)
        else { return }
        Task {
            do { try await socket.send(.string(string)) }
            catch { await failed(error, socket: socket) }
        }
    }

    private func receiveNext(from activeSocket: URLSessionWebSocketTask) async {
        guard socket === activeSocket, !Task.isCancelled else { return }
        do {
            let message = try await activeSocket.receive()
            guard socket === activeSocket else { return }
            isConnected = true
            lastError = nil
            switch message {
            case .string(let string):
                if
                    let data = string.data(using: .utf8),
                    let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                {
                    onEvent?(object)
                }
            case .data(let data):
                onAudio?(data)
            @unknown default:
                break
            }
            await receiveNext(from: activeSocket)
        } catch {
            await failed(error, socket: activeSocket)
        }
    }

    private func failed(_ error: Error, socket activeSocket: URLSessionWebSocketTask) async {
        guard socket === activeSocket else { return }
        isConnected = false
        lastError = error.localizedDescription
        socket = nil
        guard shouldReconnect, !Task.isCancelled else { return }
        try? await Task.sleep(for: .seconds(2))
        if let lastRequest, shouldReconnect {
            connect(request: lastRequest)
        }
    }
}
