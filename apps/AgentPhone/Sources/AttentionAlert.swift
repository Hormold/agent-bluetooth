import AppKit
import UserNotifications

/// The agent on a call needs you *now* (a person picked up, or it has a
/// question): the app comes to the front, the Dock icon bounces until you
/// look, a sound repeats and a time-sensitive banner shows. It all stops
/// when you act (mic on, answer sent) or the agent clears it.
@MainActor
final class AttentionAlert {
    private let center = UNUserNotificationCenter.current()
    private var sound: NSSound?
    private var request: Int?
    private static let id = "call-attention"

    func start(_ attention: CallAttention) {
        stop()
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows where window.isVisible || window.isMiniaturized {
            window.deminiaturize(nil)
            window.orderFrontRegardless()
        }
        request = NSApp.requestUserAttention(.criticalRequest)
        let tone = NSSound(named: attention.kind == .person ? "Sosumi" : "Glass")
        tone?.loops = true
        tone?.play()
        sound = tone

        let content = UNMutableNotificationContent()
        content.title = attention.title
        content.body = attention.text
        content.interruptionLevel = .timeSensitive
        content.sound = nil  // the looping tone is the sound
        center.add(UNNotificationRequest(identifier: Self.id, content: content, trigger: nil))
    }

    func stop() {
        sound?.stop()
        sound = nil
        if let request { NSApp.cancelUserAttentionRequest(request) }
        request = nil
        center.removeDeliveredNotifications(withIdentifiers: [Self.id])
    }
}

/// What the agent needs from you (daemon event call.attention).
struct CallAttention: Equatable {
    enum Kind { case person, question }

    let kind: Kind
    let text: String

    var title: String {
        kind == .person ? "A person is on the line — turn your mic on" : "The agent has a question"
    }
}
