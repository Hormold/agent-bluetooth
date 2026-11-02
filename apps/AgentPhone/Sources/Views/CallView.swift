import SwiftUI

struct CallView: View {
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
        .overlay(alignment: .topTrailing) {
            if !ringingIncoming {
                Button { toggle(.stats) } label: {
                    Image(systemName: "chart.bar.xaxis")
                        .font(.system(size: 13))
                        .foregroundStyle(panel == .stats ? Color.accentColor : .secondary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(8)
                .help("Call stats")
                .accessibilityLabel("Call stats")
            }
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
