import AVFoundation
import Foundation
import SwiftUI

/// The app's brain: mirrors the daemon over SSE + polling, and drives calls
/// (answer = join the call's LiveKit room; the daemon picks up on join).
@MainActor
final class CallCenter: ObservableObject {
    /// Status polling is only a safety net: phone and call changes arrive over
    /// SSE and trigger a refresh. Poll fast only while that stream is down.
    private static let pollWhileStreamDown = Duration.seconds(5)
    private static let pollWhileStreamUp = Duration.seconds(30)
    private static let activityLimit = 200
    private static let macIdentity = "mac"

    @Published var phone = Phone(connected: false)
    @Published var call: Call? {
        didSet { alert.update(call) }  // ring + notify for incoming calls
    }
    @Published var contacts: [Contact] = [] {
        didSet { contactIndex = nil }  // names/numbers may change without the count changing
    }
    @Published var history: [HistoryItem] = []
    @Published var directory: DirectoryInfo?
    @Published var box: BoxInfo?
    @Published var activity: [Event] = []
    @Published var phones: [PairedPhone] = []
    @Published var phonesLoaded = false  // don't claim "pair your phone" before we know
    @Published var settings: AppSettings?
    @Published var daemonOnline = false
    @Published var error: String?
    @Published var settingsOpen = false  // the Settings sheet; also opened with ⌘,

    @Published var host: String {
        didSet {
            savePrefs()
            reconnect()
        }
    }
    @Published var apiKey: String {
        didSet {
            savePrefs()
            reconnect()
        }
    }

    let live = LiveKitRoom()
    let readiness = Readiness()
    private let alert = IncomingCallAlert()
    private let attentionAlert = AttentionAlert()
    /// The agent on the call needs you now; drives the red banner and the alert.
    @Published var attention: CallAttention? {
        didSet {
            if let attention, attention != oldValue { attentionAlert.start(attention) }
            if attention == nil { attentionAlert.stop() }
        }
    }
    private var api: APIClient
    private var stream: EventStream?
    private var poll: Task<Void, Never>?
    private var streamUp = false
    private var contactIndex: PhoneBookIndex?

    init() {
        var prefs = AppPrefs.load()
        #if DEBUG
        if let demoHost = DemoMode.host { prefs.host = demoHost }  // screenshots: never saved
        #endif
        host = prefs.host
        apiKey = prefs.key
        api = APIClient(base: URL(string: prefs.host) ?? DaemonDefaults.baseURL, key: prefs.key)
        // Ask for the mic up front so the first call can publish audio without a
        // mid-call permission stall.
        AVCaptureDevice.requestAccess(for: .audio) { _ in
            // The answer is read again by the setup checklist.
        }
        if let snap = DemoMode.directory ?? DirectorySnapshot.loadFile() {
            contacts = snap.contacts
            history = snap.history
        }
        alert.onAnswer = { [weak self] in self?.answer() }
        alert.onDecline = { [weak self] in self?.hangup() }
        reconnect()
        restartPolling()
    }

    /// (Re)start the status poll at the pace the stream's state calls for, so a
    /// dropped stream gets the fast pace now, not after a 30 s sleep.
    private func restartPolling() {
        poll?.cancel()
        let interval = streamUp ? Self.pollWhileStreamUp : Self.pollWhileStreamDown
        poll = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)  // cancellation ends the loop on the next check
                guard !Task.isCancelled else { return }
                await self?.refresh()
            }
        }
    }

    private func reconnect() {
        guard let url = URL(string: host) else { return }
        api = APIClient(base: url, key: apiKey)
        stream?.stop()
        let events = EventStream(
            url: api.eventsURL,
            onEvent: { [weak self] event in self?.handle(event) },
            onConnection: { [weak self] online in
                guard let self else { return }
                daemonOnline = online
                guard streamUp != online else { return }
                streamUp = online
                restartPolling()
            })
        events.start()
        stream = events
        Task { await refresh() }
    }

    private func savePrefs() {
        if DemoMode.host == nil { AppPrefs(host: host, key: apiKey).save() }
    }
}

// MARK: - Mirroring the box

extension CallCenter {
    func refresh() async {
        do {
            let status = try await api.status()
            phone = status.phone
            // Prefer calls[]: it carries the LiveKit room name; phone.call does not.
            call = status.calls.first ?? status.phone.call
            if !daemonOnline { loadDeviceState() }  // just found the box: learn paired phones
            daemonOnline = true
        } catch {
            Log.api.info("status: \(error.localizedDescription, privacy: .public)")
            daemonOnline = false
        }
    }

    /// Pull the directory + device state. Called when opening those screens so
    /// we don't poll the phone needlessly.
    func loadDirectory() {
        // The box serves its last sync instantly; we keep a local copy too so a
        // relaunch shows the list before the network answers.
        let client = api
        Task {
            async let contactsReply = Self.quietly("contacts", client.contacts)
            async let historyReply = Self.quietly("history", client.history)
            async let infoReply = Self.quietly("directory", client.directoryInfo)
            let (newContacts, newHistory, info) = await (contactsReply, historyReply, infoReply)
            if DemoMode.directory == nil {  // demo screenshots keep their fake phonebook
                if let newContacts { contacts = newContacts }
                if let newHistory { history = newHistory }
                if newContacts != nil || newHistory != nil {
                    let snapshot = DirectorySnapshot(contacts: contacts, history: history)
                    Task.detached(priority: .utility) { snapshot.save() }  // thousands of contacts: off the main thread
                }
            }
            if let info { directory = info }
        }
    }

    /// Name from the phonebook for a number (matched on the last 10 digits).
    func contactName(for number: String) -> String? {
        if contactIndex == nil { contactIndex = PhoneBookIndex(contacts) }
        return contactIndex?.name(for: number)
    }

    func syncDirectory() {
        let client = api
        run {
            try await client.syncDirectory()
            if let info = await Self.quietly("directory", client.directoryInfo) { self.directory = info }
        }
    }

    /// Status tab: box + phone + sync state, and the recent event history.
    func loadStatus() {
        let client = api
        Task {
            async let boxReply = Self.quietly("debug", client.box)
            async let directoryReply = Self.quietly("directory", client.directoryInfo)
            async let statusReply = Self.quietly("status", client.status)
            let (newBox, newDirectory, status) = await (boxReply, directoryReply, statusReply)
            if let newBox { box = newBox }
            if let newDirectory { directory = newDirectory }
            if let status {
                phone = status.phone
                activity = status.recent.reversed()
            }
        }
    }

    func loadDeviceState() {
        let client = api
        Task {
            if let paired = await Self.quietly("phones", client.phones) {
                phones = paired
                phonesLoaded = true
            }
        }
        Task { _ = await fetchSettings() }
    }

    /// Fresh settings from the box (nil if unreachable). The settings form
    /// waits for this so it never starts editing from stale or empty values.
    func fetchSettings() async -> AppSettings? {
        guard let fresh = await Self.quietly("settings", api.settings) else { return nil }
        settings = fresh
        return fresh
    }

    /// Point the box at the LiveKit dev server on this Mac.
    func useLocalLiveKit() {
        useLiveKit(
            url: DaemonDefaults.liveKitURL, key: DaemonDefaults.liveKitDevKey, secret: DaemonDefaults.liveKitDevSecret)
    }

    /// Point the box at a LiveKit Cloud project (needed for hosted voice agents).
    func useCloudLiveKit(url: String, key: String, secret: String) {
        useLiveKit(url: url, key: key, secret: secret)
    }

    private func useLiveKit(url: String, key: String, secret: String) {
        saveSettings([
            "livekit": [
                "url": .string(url), "client_url": .string(url), "api_key": .string(key), "api_secret": .string(secret),
            ]
        ])
    }

    func pairNewPhone() { run { try await self.api.pair() } }
    func forget(_ address: String) {
        run {
            try await self.api.forget(address)
            self.loadDeviceState()
        }
    }

    func saveSettings(_ changes: JSONObject) {
        run { self.settings = try await self.api.saveSettings(changes) }
    }
}

// MARK: - Events

extension CallCenter {
    private func handle(_ event: Event) {
        activity.insert(event, at: 0)
        if activity.count > Self.activityLimit { activity.removeLast(activity.count - Self.activityLimit) }
        switch event.type {
        case EventType.callRinging, EventType.callDialing, EventType.callActive:
            if let object = event.data["call"]?.objectValue { updateCall(Call(json: object)) }
        case EventType.callEnded:
            endCall()
        case EventType.callFailed:
            callFailed(event)
        case EventType.roomOpened:
            roomOpened(event)
        case EventType.phoneConnected, EventType.phoneDisconnected, EventType.phoneStatus:
            Task { await refresh() }
        case EventType.directorySynced:
            loadDirectory()
        case EventType.callAttention:
            switch event.str("kind") {
            case "person": attention = CallAttention(kind: .person, text: event.str("text") ?? "")
            case "question": attention = CallAttention(kind: .question, text: event.str("text") ?? "")
            default: attention = nil
            }
        default:
            break  // unknown or informational: shown in Activity only
        }
    }

    /// Call events carry the phone's record, whose room and agent are always
    /// empty; keep what the box already told us about this call.
    private func updateCall(_ update: Call) {
        var update = update
        if let current = call, current.id == update.id {
            if update.room == nil { update.room = current.room }
            if update.agentName.isEmpty { update.agentName = current.agentName }
        }
        call = update
    }

    private func roomOpened(_ event: Event) {
        guard let room = event.str("room"), !room.isEmpty else { return }
        if let id = event.callID, let current = call, current.id != id { return }  // another call's room
        call?.room = room
        if let agent = event.str("agent_name"), !agent.isEmpty { call?.agentName = agent }
        // Outbound calls you placed: join the room automatically so you can talk.
        // An agent's call: join as a listener (mic off), so you always hear it.
        // Incoming calls wait for the Answer tap.
        if call?.hasAgent == true {
            run { try await self.joinRoom(room, mic: false) }
        } else if event.str("direction") == "out" || call?.direction == "out" {
            run { try await self.joinRoom(room) }
        }
    }

    /// The box couldn't place, answer or keep a call: say why, and drop the
    /// call screen if it was this call.
    private func callFailed(_ event: Event) {
        error = event.str("error") ?? event.str("reason") ?? "Call failed"
        guard let current = call else { return }
        if let id = event.callID, id != current.id { return }
        if event.callID != nil || current.state == CallState.dialing { endCall() }
    }

    private func endCall() {
        call = nil
        attention = nil
        Task { await live.leave() }
    }
}

// MARK: - Actions

extension CallCenter {
    func dial(_ number: String) {
        let trimmed = number.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        run { try await self.api.dial(trimmed) }
    }

    func answer() {
        guard let call else { return }
        run {
            let room = try await self.resolveRoom(call)
            try await self.joinRoom(room)
        }
    }

    /// Join a LiveKit room by name (fetch a token, connect, publish mic).
    private func joinRoom(_ room: String, mic: Bool = true) async throws {
        guard !live.joined, !live.joining else { return }
        let info = try await api.token(room: room, identity: Self.macIdentity)
        try await live.join(url: info.url, token: info.token, mic: mic)
    }

    /// In the room: native LiveKit DTMF (the box relays it to the phone, like a
    /// SIP trunk). Not in the room: straight to the box API.
    func sendDTMF(_ digit: String) {
        run {
            if self.live.joined { try await self.live.publishDTMF(digit) } else { try await self.api.dtmf(digit) }
        }
    }

    func hangup() {
        run {
            try await self.api.hangup()
            await self.live.leave()
        }
    }

    func toggleMute() {
        if live.muted, attention?.kind == .person { attention = nil }  // you're talking now
        run { try await self.live.toggleMute() }
    }

    func toggleListening() { run { try await self.live.setListening(!self.live.listening) } }

    /// Answer the agent's question: goes to the agent, not onto the line.
    func answerAgent(_ text: String) {
        say(text)
        attention = nil
    }

    /// A line for the agent on the call: it stops and says this instead.
    func say(_ text: String) {
        let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        run { try await self.api.say(line) }
    }

    private func resolveRoom(_ call: Call) async throws -> String {
        if let room = call.room { return room }
        // The box opens the room a moment after the call appears: wait for it.
        for _ in 0..<10 {
            let status = try await api.status()
            if let room = status.calls.first(where: { $0.id == call.id })?.room { return room }
            try await Task.sleep(for: .milliseconds(300))
        }
        throw APIError.server(status: 0, message: "room not ready")
    }

    /// Run a user action; a failure shows up in the error banner.
    private func run(_ operation: @escaping @MainActor () async throws -> Void) {
        Task {
            do {
                try await operation()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// A background refresh: failures are logged, not shown, and read as nil.
    nonisolated private static func quietly<T: Sendable>(
        _ what: String, _ operation: @Sendable () async throws -> T
    ) async -> T? {
        do {
            return try await operation()
        } catch {
            Log.api.info("\(what, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }
}

/// Contact names by the last 10 digits of each of their numbers.
private struct PhoneBookIndex {
    private let names: [String: String]

    init(_ contacts: [Contact]) {
        var byNumber: [String: String] = [:]
        for contact in contacts {
            for key in contact.numbers.compactMap(Self.key) where byNumber[key] == nil {
                byNumber[key] = contact.name
            }
        }
        names = byNumber
    }

    func name(for number: String) -> String? { Self.key(number).flatMap { names[$0] } }

    /// Too few digits to tell numbers apart: no match rather than a wrong one.
    private static func key(_ number: String) -> String? {
        let key = String(number.filter(\.isNumber).suffix(10))
        return key.count >= 7 ? key : nil
    }
}
