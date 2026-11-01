import Foundation

struct Indicators: Decodable {
    var signal: Int?
    var battchg: Int?
    var service: Int?
    var roam: Int?
}

struct Phone: Decodable {
    var connected: Bool
    var peer: String?
    var call: Call?
    var indicators: Indicators?
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

struct Contact: Decodable, Identifiable {
    var name: String
    var numbers: [String]
    var id: String { name + (numbers.first ?? "") }
    var primary: String { numbers.first ?? "" }
}

struct HistoryItem: Decodable, Identifiable {
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
}
