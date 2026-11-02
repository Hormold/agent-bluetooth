import Foundation

struct Indicators: Decodable {
    var signal: Int?
    var battchg: Int?
    var service: Int?
    var roam: Int?
}

struct Phone: Decodable {
    var connected: Bool
    var radio: Bool?
    var radio_error: RadioError?
    var name: String?
    var peer: String?
    var call: Call?
    var indicators: Indicators?
    var audio_stats: LineStats?
}

/// Bluetooth side of a live call, from the box (phone.audio_stats).
struct LineStats: Decodable {
    var codec: String?
    var link: String?
    var sample_rate: Int
    var seconds: Double
    var delivered_pct: Double
    var dropped: Int
    var to_phone_ms: Int
    var bridge_buffer_ms: Int
    var box_load: Double?
}

struct Call: Decodable, Identifiable, Equatable {
    var id: String
    var direction: String   // "in" | "out"
    var number: String
    var name: String
    var state: String       // ringing | dialing | active | ended
    var room: String?

    var incoming: Bool { direction == "in" }
    var display: String { name.isEmpty ? (number.isEmpty ? "Unknown" : number) : name }
}

struct Status: Decodable {
    var phone: Phone
    var calls: [Call]
    var recent: [Event]
}

struct Event: Decodable, Identifiable {
    let type: String
    let data: [String: JSON]
    let ts: Double
    var id: String { "\(type)-\(ts)" }

    func str(_ key: String) -> String? { data[key]?.stringValue }
}

/// Minimal JSON value so we can read arbitrary event payloads.
enum JSON: Decodable {
    case string(String), number(Double), bool(Bool), object([String: JSON]), array([JSON]), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode([String: JSON].self) { self = .object(v) }
        else if let v = try? c.decode([JSON].self) { self = .array(v) }
        else { self = .null }
    }

    var stringValue: String? {
        switch self {
        case .string(let s): return s
        case .number(let n): return n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): return String(b)
        default: return nil
        }
    }
    var objectValue: [String: JSON]? { if case .object(let o) = self { return o }; return nil }
}

struct RecentCall: Identifiable {
    let id = UUID()
    let number: String
    let name: String
    let direction: String
    let at: Date
    var display: String { name.isEmpty ? number : name }
}

struct Contact: Codable, Identifiable {
    var name: String
    var numbers: [String]
    var id: String { name + (numbers.first ?? "") }
    var primary: String { numbers.first ?? "" }
}

struct HistoryItem: Codable, Identifiable {
    var name: String
    var number: String
    var kind: String   // received | dialed | missed
    var at: String
    var id: String { number + at + kind }
    var display: String { name.isEmpty ? number : name }
}

struct PairedPhone: Decodable, Identifiable {
    var address: String
    var name: String
    var connected: Bool
    var id: String { address }
    var display: String { name.isEmpty ? String(address.split(separator: "/").first ?? "") : name }
}

struct AppSettings: Decodable {
    struct LiveKit: Decodable { var url: String; var client_url: String; var api_key: String; var api_secret: String }
    var incoming: String
    var outgoing: String
    var agent_name: String
    var play_file: String
    var answer: String
    var livekit: LiveKit
    var summary: String?  // box's plain-words description of what happens on the next call
    var rules: [CallRule]?
}

/// Server-side directory sync state (GET /api/directory).
struct DirectoryInfo: Decodable {
    var counts: [String: Int]
    var synced_at: [String: Double]
    var duration: [String: Double]
    var syncing: String?
    var last_error: String?
    var phone: String?
}

/// Last directory the app saw, kept on disk so launch shows it instantly.
struct DirectorySnapshot: Codable {
    var contacts: [Contact]
    var history: [HistoryItem]

    private static var url: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("AgentPhone", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("directory.json")
    }

    static func load() -> DirectorySnapshot? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(DirectorySnapshot.self, from: data)
    }

    func save() {
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: Self.url, options: [.atomic, .completeFileProtection])
    }
}

/// The box itself, from GET /api/debug (logs/events are for agents, not shown).
struct BoxInfo: Decodable {
    struct Config: Decodable { var transport: String?; var bridge: String?; var messages: String? }
    var uptime_s: Int
    var config: Config
}

/// Box address + API key. A file in Application Support, not UserDefaults:
/// sandboxed builds signed for local runs silently drop UserDefaults writes,
/// so the address kept resetting on every launch.
struct AppPrefs: Codable {
    static let defaultHost = "http://127.0.0.1:8080"  // daemon on this Mac
    var host: String = defaultHost
    var key: String = ""

    private static var url: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("AgentPhone", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("prefs.json")
    }

    static func load() -> AppPrefs {
        guard let data = try? Data(contentsOf: url),
              var p = try? JSONDecoder().decode(AppPrefs.self, from: data) else { return AppPrefs() }
        if p.host.trimmingCharacters(in: .whitespaces).isEmpty { p.host = defaultHost }
        return p
    }

    func save() {
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: Self.url, options: [.atomic, .completeFileProtection])
    }
}

/// Why the Bluetooth radio isn't up, with the next step (from the box).
struct RadioError: Decodable, Equatable {
    var error: String
    var hint: String
}

/// Route a call by who calls (box setting `rules`, first match wins).
struct CallRule: Codable, Equatable {
    var direction: String?
    var when_number: String?
    var when_contact: Bool?
    var mode: String?
    var action: String?
    var agent_name: String?
    var play_file: String?

    /// The one rule the app manages: what to do with numbers not in contacts.
    var isUnknownNumbersRule: Bool {
        when_contact == false && when_number == nil && (direction ?? "in") == "in"
    }
}
