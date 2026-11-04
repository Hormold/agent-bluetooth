import Foundation

/// Any JSON value. Event payloads and call rules are read and written through
/// it, so keys the app doesn't know about survive a round trip to the box.
enum JSON: Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: Self])
    case array([Self])
    case null

    var stringValue: String? {
        switch self {
        case .string(let text): text
        case .number(let value):
            // Whole numbers print without ".0"; Int(_:) traps outside its range, so only small ones.
            if value == value.rounded(), abs(value) < 1e15 { String(Int(value)) } else { String(value) }
        case .bool(let flag): String(flag)
        default: nil
        }
    }

    var boolValue: Bool? {
        if case .bool(let flag) = self { flag } else { nil }
    }

    var objectValue: JSONObject? {
        if case .object(let object) = self { object } else { nil }
    }
}

extension JSON: Codable {
    init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let text = try? container.decode(String.self) {
            self = .string(text)
        } else if let flag = try? container.decode(Bool.self) {
            self = .bool(flag)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let object = try? container.decode([String: Self].self) {
            self = .object(object)
        } else {
            self = .array(try container.decode([Self].self))
        }
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let text): try container.encode(text)
        case .number(let value): try container.encode(value)
        case .bool(let flag): try container.encode(flag)
        case .object(let object): try container.encode(object)
        case .array(let array): try container.encode(array)
        case .null: try container.encodeNil()
        }
    }
}

extension JSON: ExpressibleByStringLiteral, ExpressibleByBooleanLiteral, ExpressibleByDictionaryLiteral {
    init(stringLiteral value: String) { self = .string(value) }
    init(booleanLiteral value: Bool) { self = .bool(value) }
    init(dictionaryLiteral elements: (String, Self)...) {
        self = .object(Dictionary(elements) { _, last in last })
    }
}

/// A JSON object: request bodies and event payloads.
typealias JSONObject = [String: JSON]
