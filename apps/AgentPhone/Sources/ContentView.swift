import SwiftUI

struct ContentView: View {
    @EnvironmentObject var center: CallCenter
    @State private var showSettings = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(showSettings: $showSettings)
            Divider().opacity(0.5)
            ZStack {
                if let call = center.call, call.state != "ended" {
                    CallView(call: call)
                        .transition(.asymmetric(insertion: .scale(scale: 0.96).combined(with: .opacity),
                                                removal: .opacity))
                } else {
                    HomeView()
                        .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.22), value: center.call?.id)
            .animation(.easeOut(duration: 0.22), value: center.call?.state)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(.background)
        .sheet(isPresented: $showSettings) { SettingsView() }
        .overlay(alignment: .top) {
            if let err = center.error {
                Text(err).font(.caption).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.red, in: Capsule())
                    .padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .allowsHitTesting(false)   // never block the call buttons below
                    .task { try? await Task.sleep(nanoseconds: 4_000_000_000); center.error = nil }
            }
        }
        .animation(.easeOut(duration: 0.2), value: center.error)
    }
}

// MARK: - Status bar
private struct StatusBar: View {
    @EnvironmentObject var center: CallCenter
    @Binding var showSettings: Bool

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(center.phone.connected ? .green : (center.daemonOnline ? .orange : .secondary))
                .frame(width: 8, height: 8)
                .animation(.easeOut(duration: 0.2), value: center.phone.connected)
            Text(center.phone.connected ? (center.phone.peer.map(prettyPeer) ?? "Phone") :
                    (center.daemonOnline ? "Waiting for phone" : "Box offline"))
                .font(.subheadline.weight(.medium))
                .lineLimit(1)
            Spacer()
            if let s = center.phone.indicators?.signal { SignalBars(level: s) }
            if let b = center.phone.indicators?.battchg { BatteryDot(level: b) }
            Button { showSettings = true } label: {
                Image(systemName: "gearshape").font(.system(size: 13))
            }
            .buttonStyle(.plain).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
    }

    func prettyPeer(_ p: String) -> String { "Phone \u{00B7} " + p.split(separator: "/").first.map(String.init)!.suffix(8) }
}

private struct SignalBars: View {
    let level: Int  // 0..5
    var body: some View {
        HStack(alignment: .bottom, spacing: 1.5) {
            ForEach(1...4, id: \.self) { i in
                RoundedRectangle(cornerRadius: 1)
                    .fill(i <= level ? Color.primary.opacity(0.8) : Color.secondary.opacity(0.25))
                    .frame(width: 3, height: CGFloat(3 + i * 2))
            }
        }
    }
}

private struct BatteryDot: View {
    let level: Int  // 0..5
    var body: some View {
        Image(systemName: level <= 1 ? "battery.25" : (level <= 3 ? "battery.50" : "battery.100"))
            .font(.system(size: 13)).foregroundStyle(.secondary)
    }
}

// MARK: - Home (dialer + recents)
private struct HomeView: View {
    @EnvironmentObject var center: CallCenter
    @State private var number = ""
    @State private var tab = 0  // 0 = recents, 1 = contacts, 2 = status

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 8) {
                TextField("Enter a number", text: $number)
                    .textFieldStyle(.plain)
                    .font(.title3.monospacedDigit())
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 12))
                    .onSubmit { call(number) }
                CallButton(enabled: center.phone.connected && !number.isEmpty) { call(number) }
            }
            .padding(.horizontal, 16).padding(.top, 16)

            Picker("", selection: $tab) {
                Text("Recents").tag(0)
                Text("Contacts").tag(1)
                Text("Status").tag(2)
            }
            .pickerStyle(.segmented).labelsHidden().padding(.horizontal, 16)

            Group {
                switch tab {
                case 0: recentsList
                case 1: contactsList
                default: StatusView()
                }
            }
        }
        .onAppear { center.loadDirectory() }
        .onChange(of: tab) { _, _ in center.loadDirectory() }
        .onChange(of: center.phone.connected) { _, connected in if connected { center.loadDirectory() } }
    }

    @ViewBuilder private var recentsList: some View {
        if center.history.isEmpty {
            EmptyHint(icon: "clock.arrow.circlepath", title: "No call history",
                      subtitle: center.phone.connected ? "Pull-to-date from your phone." : "Connect your phone first.")
        } else {
            List(center.history) { h in
                CallRow(icon: icon(for: h.kind), tint: tint(for: h.kind), title: h.display, subtitle: subtitle(h)) {
                    call(h.number)
                }
            }.listStyle(.plain).scrollContentBackground(.hidden)
        }
    }

    @ViewBuilder private var contactsList: some View {
        if center.contacts.isEmpty {
            EmptyHint(icon: "person.crop.circle", title: "No contacts",
                      subtitle: center.directory?.syncing == "contacts" ? "Syncing from your phone\u{2026}"
                        : (center.phone.connected ? "Not synced yet." : "Connect your phone first."))
        } else {
            List(center.contacts) { c in
                CallRow(icon: "person.fill", tint: .secondary, title: c.name, subtitle: c.primary) { call(c.primary) }
            }.listStyle(.plain).scrollContentBackground(.hidden)
        }
    }

    func call(_ n: String) { number = n; center.dial(n) }
    func icon(for kind: String) -> String {
        kind == "missed" ? "phone.down.fill" : (kind == "dialed" ? "phone.arrow.up.right" : "phone.arrow.down.left")
    }
    func tint(for kind: String) -> Color { kind == "missed" ? .red : (kind == "dialed" ? .secondary : .green) }
    func subtitle(_ h: HistoryItem) -> String { h.number == h.display ? h.kind.capitalized : "\(h.kind.capitalized) \u{00B7} \(h.number)" }
}

private struct CallRow: View {
    let icon: String, tint: Color, title: String, subtitle: String
    let onCall: () -> Void
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 12)).foregroundStyle(tint).frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title.isEmpty ? "Unknown" : title).font(.body.weight(.medium)).lineLimit(1)
                if !subtitle.isEmpty { Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            }
            Spacer()
            Button(action: onCall) { Image(systemName: "phone") }.buttonStyle(.plain).foregroundStyle(.green)
        }
        .padding(.vertical, 4).contentShape(Rectangle())
    }
}

private struct EmptyHint: View {
    let icon: String, title: String, subtitle: String
    var body: some View {
        VStack(spacing: 6) {
            Spacer()
            Image(systemName: icon).font(.largeTitle).foregroundStyle(.tertiary)
            Text(title).foregroundStyle(.secondary)
            Text(subtitle).font(.caption).foregroundStyle(.tertiary)
            Spacer()
        }.frame(maxWidth: .infinity)
    }
}

// MARK: - Status
private struct StatusView: View {
    @EnvironmentObject var center: CallCenter
    private let tick = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if !center.daemonOnline { OfflineCard(host: center.host) }
                PhoneCard(phone: center.phone, online: center.daemonOnline)
                SyncCard()
                BoxCard(box: center.daemonOnline ? center.box : nil, radio: center.phone.radio != false)
                ActivityList(events: center.activity.filter { ActivityRow.shows($0) })
            }
            .padding(16)
        }
        .onAppear { center.loadStatus() }
        .onReceive(tick) { _ in center.loadStatus() }
    }
}

private struct Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .shadow(color: .black.opacity(0.06), radius: 0.5, y: 0.5)
            .shadow(color: .black.opacity(0.05), radius: 6, y: 2)
    }
}

private struct CardTitle: View {
    let text: String
    var body: some View {
        Text(text).font(.caption.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
    }
}

private struct OfflineCard: View {
    let host: String
    var body: some View {
        Card {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "wifi.exclamationmark").foregroundStyle(.orange).frame(width: 18)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Can\u{2019}t reach the box").font(.headline)
                    Text("Make sure it\u{2019}s powered on and on the same network as this Mac. Showing the last saved contacts and calls.")
                        .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Text(host).font(.caption.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled)
                }
            }
        }
    }
}

private struct PhoneCard: View {
    let phone: Phone
    let online: Bool
    var state: (String, Color) {
        if !online { return ("Unknown while the box is offline", .gray) }
        if phone.radio == false { return ("Bluetooth dongle not responding", .red) }
        return phone.connected ? ("Connected", .green) : ("Waiting for your phone", .orange)
    }

    var body: some View {
        Card {
            HStack(spacing: 12) {
                Image(systemName: "iphone")
                    .font(.system(size: 20))
                    .frame(width: 40, height: 40)
                    .background(.quaternary.opacity(0.5), in: Circle())
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.headline)
                    HStack(spacing: 6) {
                        Circle().fill(state.1).frame(width: 7, height: 7)
                        Text(state.0).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if online, phone.connected, let ind = phone.indicators {
                    HStack(spacing: 10) {
                        SignalBars(level: ind.signal ?? 0).accessibilityLabel("Signal \(ind.signal ?? 0) of 5")
                        Image(systemName: battery(ind.battchg ?? 0)).foregroundStyle(.secondary)
                            .accessibilityLabel("Battery")
                    }
                }
            }
        }
    }

    var title: String {
        if let n = phone.name, !n.isEmpty { return n }
        guard let p = phone.peer else { return "Phone" }
        return "Phone \u{00B7} " + String(p.split(separator: "/").first ?? "").suffix(8)
    }

    func battery(_ level: Int) -> String {
        switch level { case 0: "battery.0percent"; case 1...2: "battery.25percent"
        case 3: "battery.50percent"; case 4: "battery.75percent"; default: "battery.100percent" }
    }
}

private struct SyncCard: View {
    @EnvironmentObject var center: CallCenter

    var body: some View {
        Card {
            HStack {
                CardTitle(text: "Contacts & calls")
                Spacer()
                if let s = center.directory?.syncing {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(s == "contacts" ? "Syncing contacts\u{2026}" : "Syncing calls\u{2026}")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .transition(.opacity)
                }
            }
            row("person.2", "Contacts", "contacts")
            row("clock.arrow.circlepath", "Recent calls", "history")
            if let e = center.directory?.last_error {
                Label(e, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.red).lineLimit(2)
            }
            Button("Sync now") { center.syncDirectory() }
                .controlSize(.small)
                .disabled(!center.phone.connected || center.directory?.syncing != nil)
        }
        .animation(.easeOut(duration: 0.2), value: center.directory?.syncing)
    }

    func row(_ icon: String, _ label: String, _ kind: String) -> some View {
        let d = center.directory
        let count = d?.counts[kind] ?? (kind == "contacts" ? center.contacts.count : center.history.count)
        let synced = d?.synced_at[kind].map {
            "Synced " + Date(timeIntervalSince1970: $0).formatted(.relative(presentation: .named))
        } ?? (count > 0 ? "Saved on this Mac" : "Not synced yet")
        return HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18)
            Text(label)
            Spacer()
            VStack(alignment: .trailing, spacing: 1) {
                Text("\(count)").monospacedDigit().font(.body.weight(.medium))
                Text(synced).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

private struct BoxCard: View {
    let box: BoxInfo?
    let radio: Bool

    var body: some View {
        Card {
            CardTitle(text: "Agent Bluetooth box")
            item("dot.radiowaves.left.and.right", "Bluetooth radio",
                 box == nil ? "\u{2014}" : (radio ? "Ready" : "Not responding"), box != nil && !radio ? .red : nil)
            item("waveform", "Call audio", audio(box?.config.bridge), nil)
            item("message", "Text messages", box?.config.messages == "none" || box?.config.messages == nil ? "Off" : "On", nil)
            item("clock", "Running for", box.map { uptime($0.uptime_s) } ?? "\u{2014}", nil)
        }
    }

    func item(_ icon: String, _ label: String, _ value: String, _ tint: Color?) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18)
            Text(label)
            Spacer()
            Text(value).foregroundStyle(tint ?? .secondary).monospacedDigit()
        }
    }

    func audio(_ bridge: String?) -> String {
        switch bridge {
        case "livekit": "LiveKit"
        case "record": "Recorded"
        case nil: "\u{2014}"
        default: "Off"
        }
    }

    func uptime(_ s: Int) -> String {
        if s < 60 { return "\(s) sec" }
        if s < 3600 { return "\(s / 60) min" }
        if s < 86400 { return "\(s / 3600) h \(s % 3600 / 60) min" }
        return "\(s / 86400) d \(s % 86400 / 3600) h"
    }
}

private struct ActivityList: View {
    let events: [Event]
    var body: some View {
        Card {
            CardTitle(text: "Activity")
            if events.isEmpty {
                Text("Nothing yet. Calls, texts and syncs show up here.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ForEach(events.prefix(60)) { ActivityRow(event: $0) }
            }
        }
    }
}

private struct ActivityRow: View {
    let event: Event

    static func shows(_ e: Event) -> Bool {
        !["phone.status", "call.audio", "room.closed"].contains(e.type)
    }

    var body: some View {
        let (icon, tint, title, detail) = describe(event)
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 24, height: 24)
                .background(tint.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline.weight(.medium))
                if !detail.isEmpty {
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            Text(Date(timeIntervalSince1970: event.ts), format: .dateTime.hour().minute())
                .font(.caption).monospacedDigit().foregroundStyle(.tertiary)
        }
    }

    func describe(_ e: Event) -> (String, Color, String, String) {
        let call = e.data["call"]?.objectValue
        let who = call.map { o -> String in
            let name = o["name"]?.stringValue ?? "", num = o["number"]?.stringValue ?? ""
            return name.isEmpty ? num : (num.isEmpty ? name : "\(name) \u{00B7} \(num)")
        } ?? ""
        switch e.type {
        case "phone.connected": return ("link", .green, "Phone connected", "")
        case "phone.disconnected": return ("link", .orange, "Phone disconnected", "")
        case "call.ringing": return ("phone.arrow.down.left", .green, "Incoming call", who)
        case "call.dialing": return ("phone.arrow.up.right", .blue, "Calling", who)
        case "call.active": return ("phone.fill", .green, "Call connected", who)
        case "call.ended": return ("phone.down.fill", .secondary, "Call ended", who)
        case "room.opened": return ("waveform", .blue, "Call audio ready", "")
        case "room.joined": return ("person.fill.checkmark", .blue, "Joined the call", e.str("identity") ?? "")
        case "room.left": return ("person.fill.xmark", .secondary, "Left the call", e.str("identity") ?? "")
        case "sms.received": return ("message.fill", .green, "Text from \(e.str("name") ?? e.str("sender") ?? "unknown")", e.str("text") ?? "")
        case "sms.sent": return ("paperplane.fill", .blue, "Text sent", e.str("to") ?? "")
        case "directory.synced":
            let n = e.str("count") ?? "0"
            return e.str("kind") == "contacts"
                ? ("person.2.fill", .secondary, "Contacts synced", "\(n) contacts")
                : ("clock.arrow.circlepath", .secondary, "Recent calls synced", "\(n) calls")
        case "playback.done": return ("play.fill", .secondary, "Finished playing", e.str("file") ?? "")
        case "recording.saved": return ("record.circle", .red, "Call recorded", e.str("file") ?? "")
        default: return ("circle.fill", .secondary, e.type, "")
        }
    }
}

// MARK: - Call stats (nerd panel)
private struct CallStatsPanel: View {
    let line: LineStats?
    let rtc: WebRTCStats?

    var body: some View {
        HStack(alignment: .top, spacing: 24) {
            column("Bluetooth", rows: lineRows)
            column("LiveKit", rows: rtcRows)
        }
        .padding(12)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
        .padding(.horizontal, 24)
    }

    var lineRows: [(String, String)] {
        guard let l = line else { return [("Audio", "not on the box")] }
        return [
            ("Codec", "\(l.codec ?? "?") \(l.sample_rate / 1000) kHz"),
            ("Link", l.link ?? "?"),
            ("Delivered", String(format: "%.0f%%", l.delivered_pct)),
            ("Dropped", "\(l.dropped)"),
            ("Uplink buf", "\(l.bridge_buffer_ms) ms"),
            ("To phone", "\(l.to_phone_ms) ms"),
            ("Box load", l.box_load.map { String(format: "%.2f", $0) } ?? "\u{2014}"),
        ]
    }

    var rtcRows: [(String, String)] {
        guard let r = rtc else { return [("Room", "not joined")] }
        return [
            ("Codec", r.codec.isEmpty ? "\u{2014}" : r.codec),
            ("RTT", String(format: "%.0f ms", r.rttMs)),
            ("Jitter", String(format: "%.1f ms", r.jitterMs)),
            ("Buffer", String(format: "%.0f ms", r.bufferMs)),
            ("Loss", String(format: "%.1f%%", r.lossPct)),
            ("Concealed", String(format: "%.1f%%", r.concealedPct)),
            ("Bitrate", String(format: "%.0f kbps", r.kbps)),
        ]
    }

    func column(_ title: String, rows: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
            ForEach(rows, id: \.0) { row in
                HStack(spacing: 8) {
                    Text(row.0).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                    Text(row.1).monospacedDigit()
                }
                .font(.caption)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - DTMF keypad
private struct DTMFPad: View {
    @Binding var typed: String
    let send: (String) -> Void
    private let keys = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["*", "0", "#"]]

    var body: some View {
        VStack(spacing: 10) {
            Text(typed.isEmpty ? " " : typed)
                .font(.title3.monospacedDigit()).lineLimit(1).truncationMode(.head)
                .frame(maxWidth: .infinity)
            ForEach(keys, id: \.self) { row in
                HStack(spacing: 18) {
                    ForEach(row, id: \.self) { key in
                        Button {
                            typed += key
                            send(key)
                        } label: {
                            Text(key).font(.title2.weight(.medium)).monospacedDigit()
                                .frame(width: 52, height: 52)
                                .background(.quaternary.opacity(0.5), in: Circle())
                        }
                        .buttonStyle(PressButtonStyle())
                        .accessibilityLabel("Key \(key)")
                    }
                }
            }
        }
        .padding(.horizontal, 24)
    }
}

// MARK: - Active / incoming call
private struct CallView: View {
    @EnvironmentObject var center: CallCenter
    let call: Call
    @State private var started = Date()
    @State private var panel: CallPanel = .none
    @State private var typed = ""

    enum CallPanel { case none, stats, keypad }

    var ringingIncoming: Bool { call.incoming && call.state == "ringing" }

    func toggle(_ p: CallPanel) { panel = panel == p ? .none : p }

    var body: some View {
        VStack(spacing: panel == .none ? 22 : 14) {
            Spacer(minLength: 8)
            Circle()
                .fill(.quaternary.opacity(0.5))
                .frame(width: panel == .none ? 96 : 56, height: panel == .none ? 96 : 56)
                .overlay(Text(initials(call.display))
                    .font(.system(size: panel == .none ? 34 : 20, weight: .medium)))
                .overlay(Circle().stroke(.green.opacity(ringingIncoming ? 0.6 : 0), lineWidth: 3)
                    .scaleEffect(ringingIncoming ? 1.15 : 1)
                    .opacity(ringingIncoming ? 0 : 1)
                    .animation(ringingIncoming ? .easeOut(duration: 1).repeatForever(autoreverses: false) : .default,
                               value: ringingIncoming))

            VStack(spacing: 4) {
                Text(call.display).font(.title2.weight(.semibold)).lineLimit(1)
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary).monospacedDigit()
            }

            if center.live.joined {
                LevelMeter(level: center.live.remoteLevel).frame(height: 4).padding(.horizontal, 60)
                    .accessibilityLabel("Caller audio level")
            }
            Group {
                switch panel {
                case .stats:
                    CallStatsPanel(line: center.phone.audio_stats, rtc: center.live.joined ? center.live.stats : nil)
                case .keypad:
                    DTMFPad(typed: $typed) { center.sendDTMF($0) }
                case .none:
                    EmptyView()
                }
            }
            .transition(.opacity.combined(with: .offset(y: -4)))
            Spacer(minLength: 8)

            if ringingIncoming {
                HStack(spacing: 40) {
                    RoundIconButton(symbol: "phone.down.fill", tint: .red) { center.hangup() }
                    RoundIconButton(symbol: "phone.fill", tint: .green) { center.answer() }
                }
            } else {
                HStack(spacing: 28) {
                    RoundIconButton(symbol: center.live.muted ? "mic.slash.fill" : "mic.fill",
                                    tint: center.live.muted ? .orange : .secondary, filled: false) { center.toggleMute() }
                        .accessibilityLabel(center.live.muted ? "Unmute" : "Mute")
                    RoundIconButton(symbol: "circle.grid.3x3.fill", tint: panel == .keypad ? .accentColor : .secondary,
                                    filled: false) { toggle(.keypad) }
                        .accessibilityLabel("Keypad")
                    RoundIconButton(symbol: "phone.down.fill", tint: .red) { center.hangup() }
                    if !center.live.joined {
                        RoundIconButton(symbol: "waveform", tint: .green, filled: false) { center.answer() }
                    }
                }
            }
            Spacer().frame(height: 24)
        }
        .padding()
        .animation(.easeOut(duration: 0.2), value: panel)
        .toolbar {
            Button { toggle(.stats) } label: { Image(systemName: "chart.bar.xaxis") }
                .help("Call stats").accessibilityLabel("Call stats")
        }
        .onChange(of: call.state) { _, new in if new == "active" { started = Date() } }
        .task {  // live line stats while the call screen is up
            while !Task.isCancelled {
                await center.refresh()
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    var subtitle: String {
        switch call.state {
        case "ringing": return call.incoming ? "Incoming call" : "Ringing"
        case "dialing": return "Calling\u{2026}"
        case "active": return center.live.joined ? "On call" : "Connected on phone"
        default: return call.state
        }
    }

    func initials(_ s: String) -> String {
        let parts = s.split(separator: " ")
        if parts.count >= 2 { return String(parts[0].prefix(1) + parts[1].prefix(1)).uppercased() }
        return String(s.prefix(2)).uppercased()
    }
}

private struct LevelMeter: View {
    let level: Float
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule().fill(.green)
                    .frame(width: geo.size.width * CGFloat(min(1, level * 4)))
                    .animation(.easeOut(duration: 0.1), value: level)
            }
        }
    }
}

// MARK: - Buttons
private struct CallButton: View {
    let enabled: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "phone.fill").font(.system(size: 16, weight: .semibold))
                .frame(width: 44, height: 44)
                .background(enabled ? Color.green : Color.secondary.opacity(0.3), in: Circle())
                .foregroundStyle(.white)
        }
        .buttonStyle(PressButtonStyle()).disabled(!enabled)
    }
}

private struct RoundIconButton: View {
    let symbol: String
    var tint: Color = .secondary
    var filled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 22, weight: .medium))
                .frame(width: 60, height: 60)
                .background(filled ? tint : Color.secondary.opacity(0.15), in: Circle())
                .foregroundStyle(filled ? .white : tint)
        }
        .buttonStyle(PressButtonStyle())
    }
}

/// Subtle tactile press — scale 0.96, nothing exaggerated.
struct PressButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - Settings
private struct SettingsView: View {
    @EnvironmentObject var center: CallCenter
    @Environment(\.dismiss) var dismiss

    @State private var mode = "app"          // how to handle a call
    @State private var agentName = ""
    @State private var lkURL = ""
    @State private var lkKey = ""
    @State private var lkSecret = ""

    private let modes: [(String, String, String)] = [
        ("app", "Answer in this app", "phone.circle"),
        ("agent", "Send to an AI agent", "sparkles"),
        ("play", "Play a recording", "play.circle"),
        ("record", "Record the call", "waveform.circle"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                section("Box") {
                    field("Address", text: $center.host, mono: true)
                    field("API key (optional)", text: $center.apiKey)
                    HStack(spacing: 6) {
                        Circle().fill(center.daemonOnline ? .green : .red).frame(width: 8, height: 8)
                        Text(center.daemonOnline ? "Connected" : "Not reachable")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                section("Phone") {
                    if center.phones.isEmpty {
                        Text("No phone paired yet.").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(center.phones) { p in
                        HStack {
                            Circle().fill(p.connected ? .green : .secondary).frame(width: 7, height: 7)
                            Text(p.display).font(.callout)
                            Spacer()
                            Button("Forget") { center.forget(p.address) }
                                .buttonStyle(.borderless).font(.caption).foregroundStyle(.red)
                        }
                    }
                    Button { center.pairNewPhone() } label: {
                        Label("Pair a phone", systemImage: "plus.circle")
                    }.buttonStyle(.borderless)
                    Text("Then on your phone: Bluetooth \u{2192} pair with \u{201C}Agent Bluetooth\u{201D}.")
                        .font(.caption2).foregroundStyle(.tertiary)
                }

                section("When a call comes in") {
                    ForEach(modes, id: \.0) { m in
                        ModeRow(symbol: m.2, title: m.1, selected: mode == m.0) { mode = m.0; save() }
                    }
                    if mode == "agent" {
                        field("Agent name (LiveKit dispatch)", text: $agentName)
                            .onSubmit { save() }
                    }
                }

                section("LiveKit") {
                    field("URL", text: $lkURL, mono: true)
                    field("API key", text: $lkKey, mono: true)
                    field("API secret", text: $lkSecret, mono: true)
                    Button("Save LiveKit") { save() }.buttonStyle(.bordered)
                }
            }
            .padding(20)
        }
        .frame(width: 380, height: 560)
        .overlay(alignment: .topTrailing) {
            Button("Done") { save(); dismiss() }.keyboardShortcut(.defaultAction).padding(12)
        }
        .onAppear(perform: load)
    }

    private func load() {
        center.loadDeviceState()
        if let s = center.settings {
            mode = s.incoming; agentName = s.agent_name
            lkURL = s.livekit.url; lkKey = s.livekit.api_key; lkSecret = s.livekit.api_secret
        }
    }

    private func save() {
        center.saveSettings([
            "incoming": mode, "outgoing": mode, "agent_name": agentName,
            "answer": (mode == "app" || mode == "agent") ? "on_join" : "auto",
            "livekit": ["url": lkURL, "api_key": lkKey, "api_secret": lkSecret],
        ])
    }

    @ViewBuilder private func section(_ title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    @ViewBuilder private func field(_ label: String, text: Binding<String>, mono: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            TextField("", text: text)
                .textFieldStyle(.roundedBorder)
                .font(mono ? .caption.monospaced() : .body)
        }
    }
}

private struct ModeRow: View {
    let symbol: String, title: String, selected: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 20).foregroundStyle(selected ? Color.accentColor : .secondary)
                Text(title).foregroundStyle(.primary)
                Spacer()
                if selected { Image(systemName: "checkmark").foregroundStyle(Color.accentColor) }
            }
            .padding(.vertical, 8).padding(.horizontal, 12)
            .background(selected ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.06),
                        in: RoundedRectangle(cornerRadius: 10))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressButtonStyle())
    }
}
