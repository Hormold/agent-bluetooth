import Foundation

/// What the settings sheet edits, and the minimal change set to send the box.
struct SettingsForm: Equatable {
    /// "Numbers not in contacts" when no rule overrides them.
    static let sameAsEveryone = "same"

    var mode = "app"
    var agentName = ""
    var playFile = ""
    var liveKitURL = ""
    var liveKitKey = ""
    var liveKitSecret = ""
    var unknown = sameAsEveryone  // numbers not in contacts: same | play | stall | reject
    var stallDir = ""
    var recordCalls = false
    var recordDir = ""

    init() {
        // Empty until the box's settings arrive; the sheet stays disabled meanwhile.
    }

    init(_ settings: AppSettings) {
        mode = settings.incoming
        agentName = settings.agentName
        playFile = settings.playFile
        liveKitURL = settings.livekit.url
        liveKitKey = settings.livekit.apiKey
        liveKitSecret = settings.livekit.apiSecret
        stallDir = settings.stallDir ?? ""
        recordCalls = settings.recordCalls ?? false
        recordDir = settings.recordDir ?? ""
        unknown = Self.unknownMode(settings)
    }

    private static func unknownMode(_ settings: AppSettings) -> String {
        settings.rules.first(where: \.isUnknownNumbersRule).flatMap(\.mode) ?? sameAsEveryone
    }

    /// A choice for unknown numbers that still needs its file or folder picked.
    private var unknownIsWaiting: Bool {
        (unknown == "play" && playFile.isEmpty) || (unknown == "stall" && stallDir.isEmpty)
    }

    /// Only what differs from `settings`, as a POST /api/settings body.
    func changes(from settings: AppSettings) -> JSONObject {
        var body: JSONObject = [:]
        if mode != settings.incoming {
            body["incoming"] = .string(mode)
            body["answer"] = (mode == "app" || mode == "agent") ? "on_join" : "auto"
        }
        if agentName != settings.agentName { body["agent_name"] = .string(agentName) }
        if playFile != settings.playFile { body["play_file"] = .string(playFile) }
        if stallDir != (settings.stallDir ?? "") { body["stall_dir"] = .string(stallDir) }
        if recordCalls != (settings.recordCalls ?? false) { body["record_calls"] = .bool(recordCalls) }
        if !recordDir.isEmpty, recordDir != (settings.recordDir ?? "") { body["record_dir"] = .string(recordDir) }
        if unknown != Self.unknownMode(settings), !unknownIsWaiting {
            var rules = settings.rules.filter { !$0.isUnknownNumbersRule }
            if unknown != Self.sameAsEveryone { rules.insert(.unknownNumbers(mode: unknown), at: 0) }
            body["rules"] = .array(rules.map { .object($0.fields) })
        }
        let liveKit = settings.livekit
        if liveKitURL != liveKit.url || liveKitKey != liveKit.apiKey || liveKitSecret != liveKit.apiSecret {
            body["livekit"] = [
                "url": .string(liveKitURL), "api_key": .string(liveKitKey), "api_secret": .string(liveKitSecret),
            ]
        }
        return body
    }

    /// `settings` with this form's values in, as the box will have them once
    /// the save lands (rules and summary come back from the box itself).
    func applied(to settings: AppSettings) -> AppSettings {
        var out = settings
        out.incoming = mode
        out.agentName = agentName
        out.playFile = playFile
        out.stallDir = stallDir
        out.recordCalls = recordCalls
        out.recordDir = recordDir
        out.livekit.url = liveKitURL
        out.livekit.apiKey = liveKitKey
        out.livekit.apiSecret = liveKitSecret
        return out
    }
}
