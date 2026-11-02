import AppKit
import UserNotifications

/// Makes an incoming call impossible to miss when the window isn't in front:
/// a notification with Accept / Decline, a ringtone, and a Dock bounce. All of
/// it goes away by itself once the call is answered, declined or ends anywhere.
@MainActor
final class IncomingCallAlert: NSObject, UNUserNotificationCenterDelegate {
    var onAnswer: () -> Void = {}
    var onDecline: () -> Void = {}

    private let center = UNUserNotificationCenter.current()
    private var ringingID: String?
    private var ringtone: NSSound?
    private var attention: Int?

    private static let category = "INCOMING_CALL"
    private static let answer = "ANSWER"
    private static let decline = "DECLINE"

    override init() {
        super.init()
        center.delegate = self
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: Self.category,
                actions: [
                    UNNotificationAction(identifier: Self.answer, title: "Accept", options: [.foreground]),
                    UNNotificationAction(identifier: Self.decline, title: "Decline", options: [.destructive]),
                ],
                intentIdentifiers: []
            ),
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    /// Call on every change of the current call.
    func update(_ call: Call?) {
        let ringing = call.map { $0.incoming && $0.state == "ringing" } ?? false
        if ringing, let call, ringingID != call.id {
            start(call)
        } else if !ringing, ringingID != nil {
            stop()
        }
    }

    private func start(_ call: Call) {
        stop()
        ringingID = call.id
        let content = UNMutableNotificationContent()
        content.title = "Incoming call"
        content.body = call.display
        content.categoryIdentifier = Self.category
        content.interruptionLevel = .timeSensitive
        center.add(UNNotificationRequest(identifier: call.id, content: content, trigger: nil))

        // Ring until it's handled. A system sound keeps the bundle asset-free.
        let sound = NSSound(named: "Submarine")
        sound?.loops = true
        sound?.play()
        ringtone = sound
        attention = NSApp.requestUserAttention(.criticalRequest)
    }

    private func stop() {
        ringtone?.stop()
        ringtone = nil
        if let a = attention { NSApp.cancelUserAttentionRequest(a) }
        attention = nil
        if let id = ringingID {
            center.removeDeliveredNotifications(withIdentifiers: [id])
            center.removePendingNotificationRequests(withIdentifiers: [id])
        }
        ringingID = nil
    }

    // MARK: UNUserNotificationCenterDelegate

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list]  // show even while the app is in front
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
    ) async {
        let action = response.actionIdentifier
        await MainActor.run {
            switch action {
            case Self.answer:
                onAnswer()
            case Self.decline:
                onDecline()
            default:  // tapped the banner itself: bring the call screen up
                NSApp.activate()
            }
        }
    }
}
