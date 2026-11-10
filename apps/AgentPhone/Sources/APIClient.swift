import Foundation

extension URL {
    /// A URL from a literal that is known to be valid at compile time.
    init(literal: StaticString) {
        guard let url = Self(string: "\(literal)") else { preconditionFailure("invalid URL literal \(literal)") }
        self = url
    }
}

/// Where the daemon and the local LiveKit dev server live when they run on this
/// Mac (the config LocalServices writes uses the same address and port).
enum DaemonDefaults {
    static let baseURL = URL(literal: "http://127.0.0.1:8080")
    static let liveKitURL = "ws://127.0.0.1:7880"
    static let liveKitHTTP = "http://127.0.0.1:7880"
    static let liveKitDevKey = "devkey"
    static let liveKitDevSecret = "secret"

    /// An address (box or LiveKit) that points at this Mac.
    static func isOnThisMac(_ address: String) -> Bool {
        address.contains("127.0.0.1") || address.contains("localhost")
    }
}

/// A failed request, with the box's own words when it gave any
/// (`{"error": "..."}` on a non-2xx answer).
enum APIError: LocalizedError {
    case server(status: Int, message: String)
    case notHTTP

    var errorDescription: String? {
        switch self {
        case .server(_, let message): message
        case .notHTTP: "The box sent something that isn't an HTTP answer."
        }
    }
}

/// Thin client for the Agent Bluetooth daemon HTTP API. A value: CallCenter
/// swaps in a new one when the address or key changes.
struct APIClient: Sendable {
    /// Every endpoint the app calls (daemon api/http.py).
    enum Path {
        static let status = "api/status"
        static let events = "api/events"
        static let calls = "api/calls"
        static let hangup = "api/calls/hangup"
        static let dtmf = "api/calls/dtmf"
        static let say = "api/calls/say"
        static let history = "api/calls/history"
        static let token = "api/token"
        static let contacts = "api/contacts"
        static let directory = "api/directory"
        static let directorySync = "api/directory/sync"
        static let debug = "api/debug"
        static let phones = "api/phones"
        static let pair = "api/pair"
        static let forget = "api/phones/forget"
        static let settings = "api/settings"
    }

    private static let quickTimeout: TimeInterval = 15
    private static let slowTimeout: TimeInterval = 90  // PBAP pulls from the phone are slow and serialized

    let base: URL
    let key: String

    // MARK: calls

    func status() async throws -> Status { try await send(Path.status, timeout: Self.quickTimeout) }
    func dial(_ number: String) async throws { try await post(Path.calls, ["number": .string(number)]) }
    func hangup() async throws { try await post(Path.hangup) }
    func dtmf(_ digits: String) async throws { try await post(Path.dtmf, ["digits": .string(digits)]) }
    func say(_ text: String) async throws { try await post(Path.say, ["text": .string(text)]) }
    func token(room: String, identity: String) async throws -> JoinInfo {
        try await send(Path.token, body: ["room": .string(room), "identity": .string(identity)])
    }

    // MARK: directory, devices, settings

    func contacts() async throws -> [Contact] { try await send(Path.contacts) }
    func history() async throws -> [HistoryItem] { try await send(Path.history) }
    func directoryInfo() async throws -> DirectoryInfo { try await send(Path.directory) }
    func syncDirectory() async throws { try await post(Path.directorySync) }
    func box() async throws -> BoxInfo { try await send(Path.debug) }
    func phones() async throws -> [PairedPhone] { try await send(Path.phones) }
    func pair() async throws { try await post(Path.pair) }
    func forget(_ address: String) async throws { try await post(Path.forget, ["address": .string(address)]) }
    func settings() async throws -> AppSettings { try await send(Path.settings) }
    func saveSettings(_ changes: JSONObject) async throws -> AppSettings {
        try await send(Path.settings, body: changes)
    }

    /// The SSE stream. Browsers' EventSource can't set headers, so the key
    /// rides in the query, which the daemon accepts too.
    var eventsURL: URL {
        let url = base.appendingPathComponent(Path.events)
        guard !key.isEmpty else { return url }
        return url.appending(queryItems: [URLQueryItem(name: "key", value: key)])
    }

    // MARK: transport

    /// A POST whose answer only matters for its status.
    private func post(_ path: String, _ body: JSONObject = [:]) async throws {
        _ = try await data(path, body: body, timeout: Self.quickTimeout)
    }

    /// GET (no body) or POST (body), decoded as `T`.
    private func send<T: Decodable>(
        _ path: String, body: JSONObject? = nil, timeout: TimeInterval = slowTimeout
    ) async throws -> T {
        let payload = try await data(path, body: body, timeout: body == nil ? timeout : Self.quickTimeout)
        return try JSONDecoder().decode(T.self, from: payload)
    }

    private func data(_ path: String, body: JSONObject?, timeout: TimeInterval) async throws -> Data {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.timeoutInterval = timeout
        if !key.isEmpty { request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.notHTTP }
        if let failure = Self.failure(status: http.statusCode, data: data) { throw failure }
        return data
    }

    /// Non-2xx, or a 2xx that still says `"ok": false`, as an error carrying the
    /// box's message.
    private static func failure(status: Int, data: Data) -> APIError? {
        let object = try? JSONDecoder().decode(JSONObject.self, from: data)  // non-JSON bodies are fine here
        let failed = !(200..<300).contains(status) || object?["ok"]?.boolValue == false
        guard failed else { return nil }
        let text = String(bytes: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let message = object?["error"]?.stringValue ?? (text.isEmpty ? "HTTP \(status)" : text)
        return .server(status: status, message: message)
    }
}
