import SwiftUI

struct CallView: View {
    enum CallPanel { case hidden, stats, keypad }

    @EnvironmentObject var center: CallCenter
    /// Observed directly: mute, level and stats change on the room, not on the center.
    @ObservedObject var live: LiveKitRoom
    let call: Call
    @State private var panel: CallPanel = .hidden
    @State private var typed = ""
    @State private var line = ""  // a line for the agent (`say`)

    var ringingIncoming: Bool { call.incoming && call.state == CallState.ringing }

    var body: some View {
        VStack(spacing: panel == .hidden ? 22 : 14) {
            Spacer(minLength: 8)
            avatar
            VStack(spacing: 4) {
                Text(call.display).font(.title2.weight(.semibold)).lineLimit(1)
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary).monospacedDigit()
            }

            if live.joined {
                LevelMeter(level: live.remoteLevel).frame(height: 4).padding(.horizontal, 60)
                    .accessibilityLabel("Caller audio level")
            }
            Group {
                switch panel {
                case .stats:
                    CallStatsPanel(line: center.phone.audioStats, rtc: live.joined ? live.stats : nil)
                case .keypad:
                    DTMFPad(typed: $typed) { center.sendDTMF($0) }
                case .hidden:
                    EmptyView()
                }
            }
            .transition(.opacity.combined(with: .offset(y: -4)))
            Spacer(minLength: 8)
            if call.hasAgent, call.state == CallState.active, center.attention == nil { agentLine }
            if ringingIncoming { ringingControls } else { callControls }
            Spacer().frame(height: 24)
        }
        .padding()
        .animation(.easeOut(duration: 0.2), value: panel)
        .overlay(alignment: .topTrailing) {
            if !ringingIncoming { statsButton }
        }
        .task {  // live line stats while the call screen is up
            while !Task.isCancelled {
                await center.refresh()
                try? await Task.sleep(for: .seconds(1))  // cancellation ends the loop on the next check
            }
        }
    }

    private var avatar: some View {
        Circle()
            .fill(.quaternary.opacity(0.5))
            .frame(width: panel == .hidden ? 96 : 56, height: panel == .hidden ? 96 : 56)
            .overlay(
                Text(initials(call.display))
                    .font(.system(size: panel == .hidden ? 34 : 20, weight: .medium))
            )
            .overlay(
                Circle().stroke(.green.opacity(ringingIncoming ? 0.6 : 0), lineWidth: 3)
                    .scaleEffect(ringingIncoming ? 1.15 : 1)
                    .opacity(ringingIncoming ? 0 : 1)
                    .animation(
                        ringingIncoming ? .easeOut(duration: 1).repeatForever(autoreverses: false) : .default,
                        value: ringingIncoming))
    }

    private var ringingControls: some View {
        HStack(spacing: 40) {
            RoundIconButton(symbol: "phone.down.fill", label: "Decline", tint: .red) { center.hangup() }
            RoundIconButton(symbol: "phone.fill", label: "Answer", tint: .green) { center.answer() }
        }
    }

    private var callControls: some View {
        HStack(spacing: 28) {
            RoundIconButton(
                symbol: live.muted ? "mic.slash.fill" : "mic.fill", label: live.muted ? "Mic off" : "Mic on",
                tint: live.muted ? .orange : .secondary, filled: false
            ) { center.toggleMute() }
            RoundIconButton(
                symbol: "circle.grid.3x3.fill", label: "Keypad", tint: panel == .keypad ? .accentColor : .secondary,
                filled: false
            ) { toggle(.keypad) }
            if live.joined {
                RoundIconButton(
                    symbol: live.listening ? "speaker.wave.2.fill" : "speaker.slash.fill",
                    label: live.listening ? "Sound on" : "Sound off",
                    tint: live.listening ? .secondary : .orange, filled: false
                ) { center.toggleListening() }
            }
            RoundIconButton(symbol: "phone.down.fill", label: "Hang up", tint: .red) { center.hangup() }
            if !live.joined {
                RoundIconButton(
                    symbol: call.hasAgent ? "person.wave.2.fill" : "waveform",
                    label: call.hasAgent ? "Take over" : "Talk here", tint: .green, filled: false
                ) { center.answer() }
            }
        }
    }

    /// `agent-bluetooth say` from the call screen: the agent drops what it was
    /// saying and says this instead, in its own voice.
    private var agentLine: some View {
        HStack(spacing: 8) {
            Image(systemName: "text.bubble")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            TextField("Tell the agent what to say\u{2026}", text: $line)
                .textFieldStyle(.plain)
                .onSubmit(sendLine)
                .accessibilityLabel("Line for the agent")
            Button("Say", action: sendLine)
                .disabled(line.trimmingCharacters(in: .whitespaces).isEmpty)
                .keyboardShortcut(.defaultAction)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
        .padding(.horizontal, 24)
    }

    private func sendLine() {
        center.say(line)
        line = ""
    }

    private var statsButton: some View {
        Button {
            toggle(.stats)
        } label: {
            Image(systemName: "chart.bar.xaxis")
                .font(.system(size: 13))
                .foregroundStyle(panel == .stats ? Color.accentColor : .secondary)
                .frame(width: 32, height: 32)
                .contentShape(Rectangle())
                .accessibilityHidden(true)
        }
        .buttonStyle(.plain)
        .padding(8)
        .help("Call stats")
        .accessibilityLabel("Call stats")
    }

    var subtitle: String {
        switch call.state {
        case CallState.ringing: call.incoming ? "Incoming call" : "Ringing"
        case CallState.dialing: "Calling\u{2026}"
        case CallState.active:
            live.joined ? "On call" : (call.hasAgent ? "\(call.agentName) is on the call" : "Connected on phone")
        default: call.state
        }
    }

    func toggle(_ target: CallPanel) { panel = panel == target ? .hidden : target }

    func initials(_ name: String) -> String {
        let parts = name.split(separator: " ")
        if parts.count >= 2 { return String(parts[0].prefix(1) + parts[1].prefix(1)).uppercased() }
        return String(name.prefix(2)).uppercased()
    }
}

struct LevelMeter: View {
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

// MARK: - DTMF keypad
struct DTMFPad: View {
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

// MARK: - Call stats
struct CallStatsPanel: View {
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
        guard let stats = line else { return [("Audio", "not on the box")] }
        return [
            ("Codec", "\(stats.codec ?? "?") \(stats.sampleRate / 1000) kHz"),
            ("Link", stats.link ?? "?"),
            ("Delivered", String(format: "%.0f%%", stats.deliveredPct)),
            ("Dropped", "\(stats.dropped)"),
            ("Uplink buf", "\(stats.bridgeBufferMs) ms"),
            ("To phone", "\(stats.toPhoneMs) ms"),
            ("Box load", stats.boxLoad.map { String(format: "%.2f", $0) } ?? "\u{2014}"),
        ]
    }

    var rtcRows: [(String, String)] {
        guard let room = rtc else { return [("Room", "not joined")] }
        return [
            ("Codec", room.codec.isEmpty ? "\u{2014}" : room.codec),
            ("RTT", String(format: "%.0f ms", room.rttMs)),
            ("Jitter", String(format: "%.1f ms", room.jitterMs)),
            ("Buffer", String(format: "%.0f ms", room.bufferMs)),
            ("Loss", String(format: "%.1f%%", room.lossPct)),
            ("Concealed", String(format: "%.1f%%", room.concealedPct)),
            ("Bitrate", String(format: "%.0f kbps", room.kbps)),
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
