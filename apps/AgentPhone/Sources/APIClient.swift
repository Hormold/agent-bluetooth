import Foundation

/// Thin client for the Agent Bluetooth daemon HTTP API.
actor APIClient {
    var base: URL
    var key: String

    init(base: URL, key: String = "") {
        self.base = base
        self.key = key
    }

    func update(base: URL, key: String) {
        self.base = base
        self.key = key
    }

    private func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) -> URLRequest {
        var r = URLRequest(url: base.appendingPathComponent(path))
        r.httpMethod = method
        r.timeoutInterval = 15
        if !key.isEmpty { r.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization") }
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        return r
    }

    func status() async throws -> Status {
        let (data, _) = try await URLSession.shared.data(for: request("api/status"))
        return try JSONDecoder().decode(Status.self, from: data)
    }

    @discardableResult
    func post(_ path: String, _ body: [String: Any] = [:]) async throws -> Data {
        let (data, resp) = try await URLSession.shared.data(for: request(path, method: "POST", body: body))
        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
            throw NSError(domain: "AgentPhone", code: http.statusCode,
                          userInfo: [NSLocalizedDescriptionKey: String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"])
        }
        return data
    }

    func dial(_ number: String) async throws { try await post("api/calls", ["number": number]) }
    func answer(callId: String?) async throws { try await post("api/calls/bridge", callId.map { ["call_id": $0] } ?? [:]) }
    func hangup() async throws { try await post("api/calls/hangup") }
    func dtmf(_ digits: String) async throws { try await post("api/calls/dtmf", ["digits": digits]) }
    func sendSMS(to: String, text: String) async throws { try await post("api/sms", ["to": to, "text": text]) }

    struct JoinInfo: Decodable { let url: String; let room: String; let token: String }
    func token(room: String, identity: String) async throws -> JoinInfo {
        let data = try await post("api/token", ["room": room, "identity": identity])
        return try JSONDecoder().decode(JoinInfo.self, from: data)
    }

    private func get<T: Decodable>(_ path: String, _ type: T.Type) async throws -> T {
        var req = request(path)
        req.timeoutInterval = 90  // PBAP pulls from the phone are slow and serialized
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, http.statusCode >= 400 {
            throw NSError(domain: "AgentPhone", code: http.statusCode,
                          userInfo: [NSLocalizedDescriptionKey: String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"])
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func contacts() async throws -> [Contact] { try await get("api/contacts", [Contact].self) }
    func history() async throws -> [HistoryItem] { try await get("api/calls/history", [HistoryItem].self) }
    func phones() async throws -> [PairedPhone] { try await get("api/phones", [PairedPhone].self) }
    func settings() async throws -> AppSettings { try await get("api/settings", AppSettings.self) }

    func directoryInfo() async throws -> DirectoryInfo { try await get("api/directory", DirectoryInfo.self) }
    func syncDirectory() async throws { try await post("api/directory/sync") }
    func box() async throws -> BoxInfo { try await get("api/debug", BoxInfo.self) }

    func pair() async throws { try await post("api/pair") }
    func forget(_ address: String) async throws { try await post("api/phones/forget", ["address": address]) }

    @discardableResult
    func saveSettings(_ body: [String: Any]) async throws -> AppSettings {
        let data = try await post("api/settings", body)
        return try JSONDecoder().decode(AppSettings.self, from: data)
    }

    var eventsURL: URL {
        var c = URLComponents(url: base.appendingPathComponent("api/events"), resolvingAgainstBaseURL: false)!
        if !key.isEmpty { c.queryItems = [URLQueryItem(name: "key", value: key)] }
        return c.url!
    }
}
