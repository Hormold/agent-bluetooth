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
                } else if let step = center.setupStep {
                    SetupView(step: step, showSettings: $showSettings)
                        .transition(.opacity)
                } else {
                    HomeView()
                        .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.22), value: center.call?.id)
            .animation(.easeOut(duration: 0.22), value: center.call?.state)
            .animation(.easeOut(duration: 0.22), value: center.setupStep)
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
struct StatusBar: View {
    @EnvironmentObject var center: CallCenter
    @Binding var showSettings: Bool

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(center.phone.connected ? .green : (center.daemonOnline ? .orange : .secondary))
                .frame(width: 8, height: 8)
                .animation(.easeOut(duration: 0.2), value: center.phone.connected)
            Text(statusText)
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

    private var statusText: String {
        if center.phone.connected {
            if let n = center.phone.name, !n.isEmpty { return n }
            return center.phones.first(where: { $0.connected }).map(\.display) ?? "Phone connected"
        }
        if !center.daemonOnline { return "Box offline" }
        return center.phones.isEmpty ? "No phone paired" : "Phone not connected"
    }
}

struct SignalBars: View {
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

struct BatteryDot: View {
    let level: Int  // 0..5
    var body: some View {
        Image(systemName: level <= 1 ? "battery.25" : (level <= 3 ? "battery.50" : "battery.100"))
            .font(.system(size: 13)).foregroundStyle(.secondary)
    }
}
