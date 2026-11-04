import Foundation
import OSLog

// Wire types for the daemon's HTTP API. Required fields are only the ones the
// app cannot work without; optional details decode leniently, so a new or
// changed field in one corner of a response never takes the whole screen down.

struct Indicators: Decodable, Sendable {
    var signal: Int?
    var battchg: Int?
    var service: Int?
    var roam: Int?
}

/// Why the Bluetooth radio isn't up, with the next step (from the box).
struct RadioError: Decodable, Equatable, Sendable {
    var error: String
    var hint: String
}

struct Phone: Decodable, Sendable {
    private enum CodingKeys: String, CodingKey {
        case connected, radio, name, peer, call, indicators
        case radioError = "radio_error"
        case audioStats = "audio_stats"
    }

    var connected: Bool
    var radio: Bool?
    var radioError: RadioError?
    var name: String?
    var peer: String?
    var call: Call?
    var indicators: Indicators?
    var audioStats: LineStats?

    init(connected: Bool) {
        self.connected = connected
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        connected = try container.decode(Bool.self, forKey: .connected)
        radio = container.lenient(Bool.self, forKey: .radio)
        radioError = container.lenient(RadioError.self, forKey: .radioError)
        name = container.lenient(String.self, forKey: .name)
        peer = container.lenient(String.self, forKey: .peer)
        call = container.lenient(Call.self, forKey: .call)
        indicators = container.lenient(Indicators.self, forKey: .indicators)
        audioStats = container.lenient(LineStats.self, forKey: .audioStats)
    }
}

/// Bluetooth side of a live call, from the box (phone.audio_stats).
struct LineStats: Decodable, Sendable {
    private enum CodingKeys: String, CodingKey {
        case codec, link, seconds, dropped
        case sampleRate = "sample_rate"
        case deliveredPct = "delivered_pct"
        case toPhoneMs = "to_phone_ms"
        case bridgeBufferMs = "bridge_buffer_ms"
        case boxLoad = "box_load"
    }

    var codec: String?
    var link: String?
    var sampleRate: Int
    var seconds: Double
    var deliveredPct: Double
    var dropped: Int
    var toPhoneMs: Int
    var bridgeBufferMs: Int
    var boxLoad: Double?
}

/// Call state names as the box sends them.
enum CallState {
    static let ringing = "ringing"
    static let dialing = "dialing"
    static let active = "active"
    static let ended = "ended"
}

struct Call: Decodable, Identifiable, Equatable, Sendable {
    var id: String
    var direction: String  // "in" | "out"
    var number: String
    var name: String
    var state: String  // ringing | dialing | active | ended
    /// LiveKit room of the call. nil until the box has opened one: the phone's
    /// own call record and call events carry "" here, never a joinable name.
    var room: String?

    var incoming: Bool { direction == "in" }
    var display: String { name.isEmpty ? (number.isEmpty ? "Unknown" : number) : name }

    /// From an event payload or status entry; missing fields fall back to empty.
    init(json object: JSONObject) {
        id = object["id"]?.stringValue ?? ""
        direction = object["direction"]?.stringValue ?? "in"
        number = object["number"]?.stringValue ?? ""
        name = object["name"]?.stringValue ?? ""
        state = object["state"]?.stringValue ?? ""
        room = object["room"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 }
    }

    init(from decoder: any Decoder) throws {
        self.init(json: try JSONObject(from: decoder))
    }
}

struct Status: Decodable, Sendable {
    var phone: Phone
    var calls: [Call]
    var recent: [Event]

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        phone = try container.decode(Phone.self, forKey: .phone)
        calls = container.lenient([Call].self, forKey: .calls) ?? []
        recent = container.lenient([Event].self, forKey: .recent) ?? []
    }

    private enum CodingKeys: String, CodingKey { case phone, calls, recent }
}

struct Event: Decodable, Identifiable, Sendable {
    let type: String
    let data: JSONObject
    let ts: Double
    var id: String { "\(type)-\(ts)" }

    func str(_ key: String) -> String? { data[key]?.stringValue }

    /// The call this event is about: a full `call` object, or just `call_id`.
    var callID: String? { data["call"]?.objectValue?["id"]?.stringValue ?? str("call_id") }
}

/// Event type names the app reacts to or describes (daemon core/events.py).
enum EventType {
    static let phoneConnected = "phone.connected"
    static let phoneDisconnected = "phone.disconnected"
    static let phoneStatus = "phone.status"
    static let callRinging = "call.ringing"
    static let callDialing = "call.dialing"
    static let callActive = "call.active"
    static let callEnded = "call.ended"
    static let callFailed = "call.failed"
    static let callAudio = "call.audio"
    static let roomOpened = "room.opened"
    static let roomJoined = "room.joined"
    static let roomLeft = "room.left"
    static let roomClosed = "room.closed"
    static let smsReceived = "sms.received"
    static let smsSent = "sms.sent"
    static let directorySynced = "directory.synced"
    static let playbackDone = "playback.done"
    static let recordingSaved = "recording.saved"
}

struct Contact: Codable, Identifiable, Sendable {
    var name: String
    var numbers: [String]
    var id: String { name + (numbers.first ?? "") }
    var primary: String { numbers.first ?? "" }
}

/// Call history kinds as the phone reports them (PBAP).
enum HistoryKind {
    static let received = "received"
    static let dialed = "dialed"
    static let missed = "missed"
}

struct HistoryItem: Codable, Identifiable, Sendable {
    var name: String
    var number: String
    var kind: String  // received | dialed | missed
    var at: String
    var id: String { number + at + kind }
    var display: String { name.isEmpty ? number : name }
}

struct PairedPhone: Decodable, Identifiable, Sendable {
    var address: String
    var name: String
    var connected: Bool
    var id: String { address }
    var display: String { name.isEmpty ? String(address.split(separator: "/").first ?? "") : name }
}

struct AppSettings: Decodable, Sendable {
    struct LiveKit: Decodable, Sendable {
        private enum CodingKeys: String, CodingKey {
            case url
            case apiKey = "api_key"
            case apiSecret = "api_secret"
        }

        var url: String
        var apiKey: String
        var apiSecret: String
    }

    private enum CodingKeys: String, CodingKey {
        case incoming, livekit, rules
        case agentName = "agent_name"
        case playFile = "play_file"
        case stallDir = "stall_dir"
        case recordCalls = "record_calls"
        case recordDir = "record_dir"
    }

    var incoming: String
    var agentName: String
    var playFile: String
    var stallDir: String?
    var livekit: LiveKit
    var rules: [CallRule]
    var recordCalls: Bool?
    var recordDir: String?

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        incoming = try container.decode(String.self, forKey: .incoming)
        agentName = try container.decode(String.self, forKey: .agentName)
        playFile = try container.decode(String.self, forKey: .playFile)
        livekit = try container.decode(LiveKit.self, forKey: .livekit)
        stallDir = container.lenient(String.self, forKey: .stallDir)
        rules = container.lenient([CallRule].self, forKey: .rules) ?? []  // older boxes have none
        recordCalls = container.lenient(Bool.self, forKey: .recordCalls)
        recordDir = container.lenient(String.self, forKey: .recordDir)
    }
}

/// Server-side directory sync state (GET /api/directory).
struct DirectoryInfo: Decodable, Sendable {
    private enum CodingKeys: String, CodingKey {
        case counts, duration, syncing, phone
        case syncedAt = "synced_at"
        case lastError = "last_error"
    }

    var counts: [String: Int]
    var syncedAt: [String: Double]
    var duration: [String: Double]
    var syncing: String?
    var lastError: String?
    var phone: String?
}

/// The box itself, from GET /api/debug (logs/events are for agents, not shown).
struct BoxInfo: Decodable, Sendable {
    struct Config: Decodable, Sendable {
        var transport: String?
        var bridge: String?
        var messages: String?
    }

    private enum CodingKeys: String, CodingKey {
        case config
        case uptimeSeconds = "uptime_s"
    }

    var uptimeSeconds: Int
    var config: Config
}

/// What POST /api/token returns: where and how to join a call's room.
struct JoinInfo: Decodable, Sendable {
    let url: String
    let room: String
    let token: String
}

/// Route a call by who calls (box setting `rules`, first match wins). Kept as
/// raw JSON: the box knows keys the app doesn't edit (stall_dir, play_file,
/// agent_name, ...), and saving rules must hand those back untouched.
struct CallRule: Codable, Equatable, Sendable {
    var fields: JSONObject

    /// The rule for numbers not in contacts, routed to `mode`.
    static func unknownNumbers(mode: String) -> Self {
        Self(fields: ["when_contact": false, "mode": .string(mode)])
    }

    /// What this rule does: `mode`, or its older name `action`.
    var mode: String? { fields["mode"]?.stringValue ?? fields["action"]?.stringValue }

    /// The one rule the app manages: what to do with numbers not in contacts.
    var isUnknownNumbersRule: Bool {
        fields["when_contact"]?.boolValue == false && (fields["when_number"] ?? .null) == .null
            && (fields["direction"]?.stringValue ?? "in") == "in"
    }

    init(fields: JSONObject) {
        self.fields = fields
    }

    init(from decoder: any Decoder) throws {
        fields = try JSONObject(from: decoder)
    }

    func encode(to encoder: any Encoder) throws {
        try fields.encode(to: encoder)
    }
}

extension KeyedDecodingContainer {
    /// An optional detail that must not fail the whole response: a missing,
    /// null or differently shaped value becomes nil (and is logged).
    func lenient<T: Decodable>(_ type: T.Type, forKey key: Key) -> T? {
        do {
            return try decodeIfPresent(type, forKey: key)
        } catch {
            Log.api.error(
                "ignoring \(key.stringValue, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }
}
