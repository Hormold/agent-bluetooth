import SwiftUI

/// How one box event reads in the Activity list. Unknown event types show
/// their raw name, so new daemon events appear without an app update.
struct ActivityLine {
    let icon: String
    let tint: Color
    let title: String
    let detail: String

    init(_ event: Event) {
        let who = Self.who(event.data["call"]?.objectValue)
        switch event.type {
        case EventType.phoneConnected: self.init("link", .green, "Phone connected")
        case EventType.phoneDisconnected: self.init("link", .orange, "Phone disconnected")
        case EventType.callRinging: self.init("phone.arrow.down.left", .green, "Incoming call", who)
        case EventType.callDialing: self.init("phone.arrow.up.right", .blue, "Calling", who)
        case EventType.callActive: self.init("phone.fill", .green, "Call connected", who)
        case EventType.callEnded: self.init("phone.down.fill", .secondary, "Call ended", who)
        case EventType.callFailed:
            self.init(
                "exclamationmark.triangle.fill", .red, "Call failed", event.str("error") ?? event.str("reason") ?? who)
        case EventType.roomOpened: self.init("waveform", .blue, "Call audio ready")
        case EventType.roomJoined:
            self.init("person.fill.checkmark", .blue, "Joined the call", event.str("identity") ?? "")
        case EventType.roomLeft:
            self.init("person.fill.xmark", .secondary, "Left the call", event.str("identity") ?? "")
        case EventType.smsReceived:
            let sender = event.str("name") ?? event.str("sender") ?? "unknown"
            self.init("message.fill", .green, "Text from \(sender)", event.str("text") ?? "")
        case EventType.smsSent: self.init("paperplane.fill", .blue, "Text sent", event.str("to") ?? "")
        case EventType.directorySynced:
            let count = event.str("count") ?? "0"
            if event.str("kind") == "contacts" {
                self.init("person.2.fill", .secondary, "Contacts synced", "\(count) contacts")
            } else {
                self.init("clock.arrow.circlepath", .secondary, "Recent calls synced", "\(count) calls")
            }
        case EventType.playbackDone: self.init("play.fill", .secondary, "Finished playing", event.str("file") ?? "")
        case EventType.recordingSaved: self.init("record.circle", .red, "Call recorded", event.str("file") ?? "")
        case EventType.callSay: self.init("text.bubble.fill", .blue, "Told the agent", event.str("text") ?? "")
        case EventType.callSummary:
            self.init("checkmark.seal.fill", .green, "Call with \(Self.who(event.data))", event.str("outcome") ?? "")
        default: self.init("circle.fill", .secondary, event.type)
        }
    }

    private init(_ icon: String, _ tint: Color, _ title: String, _ detail: String = "") {
        self.icon = icon
        self.tint = tint
        self.title = title
        self.detail = detail
    }

    /// "Name · number", or whichever of the two the call (or a summary) has.
    private static func who(_ call: JSONObject?) -> String {
        guard let call else { return "" }
        let name = call["name"]?.stringValue ?? ""
        let number = call["number"]?.stringValue ?? ""
        return name.isEmpty ? number : (number.isEmpty ? name : "\(name) \u{00B7} \(number)")
    }
}
