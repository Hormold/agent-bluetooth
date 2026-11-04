import Foundation

/// Server-Sent Events reader for /api/events. Hands decoded events to the main
/// actor and reconnects on its own, so the UI always reflects the daemon.
@MainActor
final class EventStream {
    private static let retryDelay = Duration.seconds(2)

    private let url: URL
    private let onEvent: (Event) -> Void
    private let onConnection: (Bool) -> Void
    private var task: Task<Void, Never>?

    init(url: URL, onEvent: @escaping (Event) -> Void, onConnection: @escaping (Bool) -> Void) {
        self.url = url
        self.onEvent = onEvent
        self.onConnection = onConnection
    }

    func start() {
        task?.cancel()
        task = Task { [weak self] in await self?.run() }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func run() async {
        while !Task.isCancelled {
            do {
                try await listen()
            } catch {
                Log.events.info("event stream dropped: \(error.localizedDescription, privacy: .public)")
            }
            guard !Task.isCancelled else { return }  // replaced or stopped: the new stream reports
            onConnection(false)
            try? await Task.sleep(for: Self.retryDelay)  // cancellation ends the loop on the next check
        }
    }

    /// One connection: returns when the daemon closes it, throws when it fails.
    private func listen() async throws {
        var request = URLRequest(url: url)
        request.timeoutInterval = .infinity
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.notHTTP }
        guard http.statusCode == 200 else {  // e.g. 401 {"error": "bad api key"}: not a live stream
            throw APIError.server(status: http.statusCode, message: "event stream: HTTP \(http.statusCode)")
        }
        onConnection(true)
        for try await line in bytes.lines where line.hasPrefix("data:") {
            let json = line.dropFirst("data:".count).trimmingCharacters(in: .whitespaces)
            do {
                onEvent(try JSONDecoder().decode(Event.self, from: Data(json.utf8)))
            } catch {
                Log.events.error("unreadable event: \(error.localizedDescription, privacy: .public)")
            }
        }
    }
}
