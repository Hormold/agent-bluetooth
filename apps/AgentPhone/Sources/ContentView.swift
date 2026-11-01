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
    @State private var tab = 0  // 0 = recents, 1 = contacts

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
            }
            .pickerStyle(.segmented).labelsHidden().padding(.horizontal, 16)

            Group {
                if tab == 0 { recentsList } else { contactsList }
            }
        }
        .onAppear { center.loadDirectory() }
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
                      subtitle: center.phone.connected ? "Loading from your phone\u{2026}" : "Connect your phone first.")
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

// MARK: - Active / incoming call
private struct CallView: View {
    @EnvironmentObject var center: CallCenter
    let call: Call
    @State private var started = Date()

    var ringingIncoming: Bool { call.incoming && call.state == "ringing" }

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Circle()
                .fill(.quaternary.opacity(0.5))
                .frame(width: 96, height: 96)
                .overlay(Text(initials(call.display)).font(.system(size: 34, weight: .medium)))
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
            }
            Spacer()

            if ringingIncoming {
                HStack(spacing: 40) {
                    RoundIconButton(symbol: "phone.down.fill", tint: .red) { center.hangup() }
                    RoundIconButton(symbol: "phone.fill", tint: .green) { center.answer() }
                }
            } else {
                HStack(spacing: 40) {
                    RoundIconButton(symbol: center.live.muted ? "mic.slash.fill" : "mic.fill",
                                    tint: center.live.muted ? .orange : .secondary, filled: false) { center.toggleMute() }
                    RoundIconButton(symbol: "phone.down.fill", tint: .red) { center.hangup() }
                    if !center.live.joined {
                        RoundIconButton(symbol: "waveform", tint: .green, filled: false) { center.answer() }
                    }
                }
            }
            Spacer().frame(height: 24)
        }
        .padding()
        .onChange(of: call.state) { _, new in if new == "active" { started = Date() } }
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
