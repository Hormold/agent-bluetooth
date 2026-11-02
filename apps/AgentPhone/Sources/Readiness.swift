import AppKit
import AVFoundation
import UserNotifications

/// Everything the app depends on, each with an honest status and the one
/// action that fixes it. Drives the "Ready to take calls" checklist.
@MainActor
final class Readiness: ObservableObject {
    enum State: Equatable {
        case ok(String)
        case needs(String, action: String?)   // what's wrong, button title (nil = nothing we can do here)
        case working(String)
        case failed(String)
    }

    enum Item: String, CaseIterable, Identifiable {
        case service, dongle, phone, audio, microphone, notifications
        var id: String { rawValue }
        var title: String {
            switch self {
            case .service: "Agent Bluetooth service"
            case .dongle: "Bluetooth dongle"
            case .phone: "Phone"
            case .audio: "Call audio (LiveKit)"
            case .microphone: "Microphone"
            case .notifications: "Notifications"
            }
        }
        var symbol: String {
            switch self {
            case .service: "server.rack"
            case .dongle: "cable.connector"
            case .phone: "iphone"
            case .audio: "waveform"
            case .microphone: "mic"
            case .notifications: "bell"
            }
        }
    }

    @Published var states: [Item: State] = [:]
    @Published var busy = false
    private var liveKitUp = false
    private var micStatus: AVAuthorizationStatus = .notDetermined
    private var notifyStatus: UNAuthorizationStatus = .notDetermined

    var problems: Int {
        states.values.filter { if case .ok = $0 { false } else { true } }.count
    }

    /// Re-check everything against the live box and the system.
    func refresh(_ center: CallCenter) async {
        micStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        notifyStatus = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        liveKitUp = await LocalServices.liveKitRunning(url: liveKitHTTP(center))
        for item in Item.allCases where !isWorking(item) { states[item] = evaluate(item, center) }
    }

    private func isWorking(_ item: Item) -> Bool {
        if case .working = states[item] { return true }
        return false
    }

    private func evaluate(_ item: Item, _ center: CallCenter) -> State {
        switch item {
        case .service:
            if center.daemonOnline { return .ok("Running") }
            return LocalServices.daemonInstalled
                ? .needs("Installed, not running", action: "Start")
                : .needs("Not installed", action: "Install")
        case .dongle:
            guard center.daemonOnline else { return .needs("Waiting for the service", action: nil) }
            if let e = center.phone.radio_error { return .needs(e.hint, action: nil) }
            return center.phone.radio == false ? .needs("Not responding. Plug it in.", action: nil) : .ok("Ready")
        case .phone:
            guard center.daemonOnline else { return .needs("Waiting for the service", action: nil) }
            if center.phone.connected { return .ok(center.phone.name.flatMap { $0.isEmpty ? nil : $0 } ?? "Connected") }
            return center.phones.isEmpty
                ? .needs("On your phone, pair with \u{201C}Agent Bluetooth\u{201D}", action: "Pair")
                : .needs("Paired, not connected. Turn on Bluetooth on the phone.", action: nil)
        case .audio:
            if isCloud(center) {
                return hasCloudKeys(center) ? .ok("LiveKit Cloud") : .needs("Add your LiveKit Cloud keys", action: nil)
            }
            if liveKitUp { return .ok("Local server running") }
            return LocalServices.liveKitInstalled
                ? .needs("Local server not running", action: "Start")
                : .needs("Needed to talk on calls here", action: "Install")
        case .microphone:
            switch micStatus {
            case .authorized: return .ok("Allowed")
            case .notDetermined: return .needs("To talk on calls", action: "Allow")
            default: return .needs("Denied. Allow it in System Settings.", action: "Open")
            }
        case .notifications:
            switch notifyStatus {
            case .authorized, .provisional: return .ok("Allowed")
            case .notDetermined: return .needs("To ring when the window is hidden", action: "Allow")
            default: return .needs("Off. Turn them on in System Settings.", action: "Open")
            }
        }
    }

    // MARK: actions

    func fix(_ item: Item, _ center: CallCenter) async {
        states[item] = .working(progress(item))
        do {
            switch item {
            case .service:
                if !LocalServices.daemonInstalled { try await LocalServices.installDaemon() }
                try await LocalServices.startDaemon()
                try await waitFor { await center.refresh(); return center.daemonOnline }
            case .audio:
                if !LocalServices.liveKitInstalled { try await LocalServices.installLiveKit() }
                try await LocalServices.startLiveKit()
                try await waitFor { await LocalServices.liveKitRunning() }
                if center.daemonOnline { center.useLocalLiveKit() }
            case .phone:
                center.pairNewPhone()
            case .microphone:
                if micStatus == .notDetermined { _ = await AVCaptureDevice.requestAccess(for: .audio) }
                else { open("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") }
            case .notifications:
                if notifyStatus == .notDetermined {
                    _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
                } else {
                    open("x-apple.systempreferences:com.apple.Notifications-Settings.extension")
                }
            case .dongle:
                break
            }
            states[item] = nil
        } catch {
            states[item] = .failed(error.localizedDescription)
            return
        }
        await refresh(center)
    }

    /// Run every fix we can do from here, in dependency order.
    func fixAll(_ center: CallCenter) async {
        busy = true
        defer { busy = false }
        for item in [Item.service, .audio, .microphone, .notifications, .phone] {
            if case .needs(_, let action?) = states[item], action != "Open" { await fix(item, center) }
        }
    }

    private func progress(_ item: Item) -> String {
        switch item {
        case .service: LocalServices.daemonInstalled ? "Starting\u{2026}" : "Installing\u{2026} (a minute)"
        case .audio: LocalServices.liveKitInstalled ? "Starting\u{2026}" : "Installing LiveKit\u{2026}"
        default: "Working\u{2026}"
        }
    }

    private func waitFor(_ check: () async -> Bool) async throws {
        for _ in 0..<30 {
            if await check() { return }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        throw LocalServices.Failure(message: "Started, but it isn't answering yet. Check ~/.agent-bluetooth/*.log")
    }

    private func open(_ url: String) {
        if let u = URL(string: url) { NSWorkspace.shared.open(u) }
    }

    // MARK: LiveKit mode

    func isCloud(_ center: CallCenter) -> Bool {
        guard let url = center.settings?.livekit.url, !url.isEmpty else { return false }
        return !(url.contains("127.0.0.1") || url.contains("localhost"))
    }

    private func hasCloudKeys(_ center: CallCenter) -> Bool {
        guard let lk = center.settings?.livekit else { return false }
        return !lk.api_key.isEmpty && !lk.api_secret.isEmpty && lk.api_key != "devkey"
    }

    private func liveKitHTTP(_ center: CallCenter) -> String {
        let url = center.settings?.livekit.url ?? "ws://127.0.0.1:7880"
        return url.replacingOccurrences(of: "ws://", with: "http://").replacingOccurrences(of: "wss://", with: "https://")
    }
}
