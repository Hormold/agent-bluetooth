import AVFoundation
import Foundation
import SwiftUI

/// The app's brain: mirrors the daemon over SSE + polling, and drives calls
/// (answer = join the call's LiveKit room; the daemon picks up on join).
@MainActor
final class CallCenter: ObservableObject {
    @Published var phone = Phone(connected: false, peer: nil, call: nil, indicators: nil)
    @Published var call: Call? {
        didSet { alert.update(call) }  // ring + notify for incoming calls
    }
    @Published var recents: [RecentCall] = []
    @Published var contacts: [Contact] = [] {
        didSet { contactIndexCount = -1 }  // names/numbers may change without the count changing
    }
    @Published var history: [HistoryItem] = []
    @Published var directory: DirectoryInfo?
    @Published var box: BoxInfo?
    @Published var activity: [Event] = []
    @Published var phones: [PairedPhone] = []
    @Published var phonesLoaded = false
    private var contactIndex: [String: String] = [:]
    private var contactIndexCount = -1  // don't claim "pair your phone" before we know
    @Published var settings: AppSettings?
    @Published var daemonOnline = false
    @Published var error: String?

    @Published var host: String {
        didSet { savePrefs(); reconnect() }
    }
    @Published var apiKey: String {
        didSet { savePrefs(); reconnect() }
    }

    let live = LiveKitRoom()
    private let alert = IncomingCallAlert()
    private var api: APIClient
    private var stream: EventStream?
    private var poll: Timer?

    init() {
        var prefs = AppPrefs.load()
        #if DEBUG
        if let demoHost = DemoMode.host { prefs.host = demoHost }  // screenshots: never saved
        #endif
        let h = prefs.host, k = prefs.key
        host = h; apiKey = k
        api = APIClient(base: URL(string: h) ?? URL(string: "http://127.0.0.1:8080")!, key: k)
        // Ask for the mic up front so the first call can publish audio without a
        // mid-call permission stall.
        AVCaptureDevice.requestAccess(for: .audio) { _ in }
        if let snap = DemoMode.directory ?? DirectorySnapshot.load() { contacts = snap.contacts; history = snap.history }
        alert.onAnswer = { [weak self] in self?.answer() }
        alert.onDecline = { [weak self] in self?.hangup() }
        start()
    }

    private func start() {
        reconnect()
        poll = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    private func reconnect() {
        guard let url = URL(string: host) else { return }
        Task { await api.update(base: url, key: apiKey) }
        stream?.stop()
        let s = EventStream(url: { [api] in
            // eventsURL is actor-isolated; snapshot synchronously via a helper.
            URL(string: "\(self.host)/api/events" + (self.apiKey.isEmpty ? "" : "?key=\(self.apiKey)"))!
        }, onEvent: { [weak self] ev in self?.handle(ev) },
           onConnection: { [weak self] ok in self?.daemonOnline = ok })
        s.start()
        stream = s
        _ = api  // silence capture note
        Task { await refresh() }
    }

    private func savePrefs() {
        if DemoMode.host == nil { AppPrefs(host: host, key: apiKey).save() }
    }

    func refresh() async {
        do {
            let st = try await api.status()
            phone = st.phone
            // Prefer calls[] — it carries the LiveKit room name; phone.call does not.
            call = st.calls.first ?? st.phone.call
            if !daemonOnline { loadDeviceState() }  // just found the box: learn paired phones
            daemonOnline = true
        } catch {
            daemonOnline = false
        }
    }

    /// Pull the directory + device state. Called when opening those screens so
    /// we don't poll the phone needlessly.
    func loadDirectory() {
        // The box serves its last sync instantly; we keep a local copy too so a
        // relaunch shows the list before the network answers.
        Task {
            async let c = try? api.contacts()
            async let h = try? api.history()
            async let i = try? api.directoryInfo()
            let (contacts, history, info) = await (c, h, i)
            if DemoMode.directory == nil {  // demo screenshots keep their fake phonebook
                if let contacts { self.contacts = contacts }
                if let history { self.history = history }
            }
            if let info { directory = info }
            if (contacts != nil || history != nil) && DemoMode.directory == nil {
                DirectorySnapshot(contacts: self.contacts, history: self.history).save()
            }
        }
    }

    /// Name from the phonebook for a number (matched on the last 10 digits).
    func contactName(for number: String) -> String? {
        let key = String(number.filter(\.isNumber).suffix(10))
        guard key.count >= 7 else { return nil }
        if contactIndexCount != contacts.count { rebuildContactIndex() }
        return contactIndex[key]
    }

    private func rebuildContactIndex() {
        var idx: [String: String] = [:]
        for c in contacts {
            for n in c.numbers {
                let k = String(n.filter(\.isNumber).suffix(10))
                if k.count >= 7, idx[k] == nil { idx[k] = c.name }
            }
        }
        contactIndex = idx
        contactIndexCount = contacts.count
    }

    func syncDirectory() {
        run {
            try await self.api.syncDirectory()
            self.directory = try? await self.api.directoryInfo()
        }
    }

    /// Status tab: box + phone + sync state, and the recent event history.
    func loadStatus() {
        Task {
            async let b = try? api.box()
            async let d = try? api.directoryInfo()
            async let s = try? api.status()
            let (box, dir, st) = await (b, d, s)
            if let box { self.box = box }
            if let dir { directory = dir }
            if let st { phone = st.phone; activity = st.recent.reversed() }
        }
    }

    func loadDeviceState() {
        Task { if let p = try? await api.phones() { phones = p; phonesLoaded = true } }
        Task { if let s = try? await api.settings() { settings = s } }
    }

    /// Fresh settings from the box (nil if unreachable). The settings form
    /// waits for this so it never starts editing from stale or empty values.
    func fetchSettings() async -> AppSettings? {
        guard let s = try? await api.settings() else { return nil }
        settings = s
        return s
    }

    func pairNewPhone() { run { try await self.api.pair() } }
    func forget(_ address: String) { run { try await self.api.forget(address); self.loadDeviceState() } }

    func saveSettings(_ body: [String: Any]) {
        run { self.settings = try await self.api.saveSettings(body) }
    }

    // MARK: events
    private func handle(_ ev: Event) {
        activity.insert(ev, at: 0)
        if activity.count > 200 { activity.removeLast(activity.count - 200) }
        switch ev.type {
        case "call.ringing", "call.dialing", "call.active":
            if let obj = ev.data["call"]?.objectValue { call = decodeCall(obj) }
        case "call.ended":
            if let obj = ev.data["call"]?.objectValue { addRecent(decodeCall(obj)) }
            call = nil
            Task { await live.leave() }
        case "room.opened":
            if let r = ev.str("room") {
                call?.room = r
                // Outbound calls: join the room automatically so you can talk.
                // Incoming calls wait for the Answer tap.
                if ev.str("direction") == "out" || call?.direction == "out" {
                    run { try await self.joinRoom(r) }
                }
            }
        case "phone.connected", "phone.disconnected", "phone.status":
            Task { await refresh() }
        case "directory.synced":
            loadDirectory()
        default: break
        }
    }

    private func decodeCall(_ o: [String: JSON]) -> Call {
        Call(id: o["id"]?.stringValue ?? "", direction: o["direction"]?.stringValue ?? "in",
             number: o["number"]?.stringValue ?? "", name: o["name"]?.stringValue ?? "",
             state: o["state"]?.stringValue ?? "", room: o["room"]?.stringValue)
    }

    private func addRecent(_ c: Call) {
        guard !c.number.isEmpty || !c.name.isEmpty else { return }
        recents.insert(RecentCall(number: c.number, name: c.name, direction: c.direction, at: Date()), at: 0)
        recents = Array(recents.prefix(50))
    }

    // MARK: actions
    func dial(_ number: String) {
        let n = number.trimmingCharacters(in: .whitespaces)
        guard !n.isEmpty else { return }
        run { try await self.api.dial(n) }
    }

    func answer() {
        guard let c = call else { return }
        run {
            let room = try await self.resolveRoom(c)
            try await self.joinRoom(room)
        }
    }

    /// Join a LiveKit room by name (fetch a token, connect, publish mic).
    private func joinRoom(_ room: String) async throws {
        guard !live.joined && !live.joining else { return }
        let info = try await api.token(room: room, identity: "mac")
        try await live.join(url: info.url, token: info.token)
    }

    /// In the room: native LiveKit DTMF (the box relays it to the phone, like a
    /// SIP trunk). Not in the room: straight to the box API.
    func sendDTMF(_ digit: String) {
        run {
            if self.live.joined { try await self.live.publishDTMF(digit) } else { try await self.api.dtmf(digit) }
        }
    }

    func hangup() {
        run { try await self.api.hangup(); await self.live.leave() }
    }

    func toggleMute() { Task { await live.toggleMute() } }


    private func resolveRoom(_ c: Call) async throws -> String {
        if let r = c.room { return r }
        return try await roomName(for: c.id)
    }

    private func roomName(for id: String) async throws -> String {
        for _ in 0..<10 {
            let st = try await api.status()
            if let r = (st.phone.call ?? st.calls.first(where: { $0.id == id }))?.room { return r }
            try await Task.sleep(nanoseconds: 300_000_000)
        }
        throw NSError(domain: "AgentPhone", code: 1, userInfo: [NSLocalizedDescriptionKey: "room not ready"])
    }

    private func run(_ op: @escaping () async throws -> Void) {
        Task {
            do { try await op() } catch { self.error = error.localizedDescription }
        }
    }
}
