import SwiftUI

struct ContentView: View {
    @EnvironmentObject var center: CallCenter
    @State private var showReadiness = false
    @EnvironmentObject var readiness: Readiness

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(showSettings: $center.settingsOpen, showReadiness: $showReadiness)
            ZStack {
                if let call = center.call, call.state != CallState.ended {
                    CallView(live: center.live, call: call)
                        .transition(
                            .asymmetric(
                                insertion: .scale(scale: 0.96).combined(with: .opacity),
                                removal: .opacity))
                } else if center.setupStep != nil || DemoMode.forceGate {
                    ReadinessView()
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
        .ignoresSafeArea(.container, edges: .top)  // header shares the titlebar row
        .sheet(isPresented: $center.settingsOpen) { SettingsView() }
        .sheet(isPresented: $showReadiness) {
            VStack(spacing: 0) {
                SheetHeader(title: "Setup") { showReadiness = false }
                ReadinessView(compact: true)
            }
            .frame(width: 420, height: 640)
        }
        .task {  // keep the header's setup chip honest
            while !Task.isCancelled {
                await readiness.refresh(center)
                try? await Task.sleep(for: .seconds(5))  // cancellation ends the loop on the next check
            }
        }
        .task {
            try? await Task.sleep(for: .seconds(1.5))  // cancelled with the view: nothing to open
            if DemoMode.openSettings { center.settingsOpen = true }
            if DemoMode.openSetup { showReadiness = true }
        }
        .overlay(alignment: .top) {
            if let err = center.error {
                Text(err).font(.caption).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.red, in: Capsule())
                    .padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .allowsHitTesting(false)  // never block the call buttons below
                    .task(id: err) {
                        // Restarted when the message changes: each one gets its own 4 s.
                        guard (try? await Task.sleep(for: .seconds(4))) != nil else { return }
                        center.error = nil
                    }
            }
        }
        .animation(.easeOut(duration: 0.2), value: center.error)
    }
}

// MARK: - Status bar
struct StatusBar: View {
    @EnvironmentObject var center: CallCenter
    @Binding var showSettings: Bool
    @Binding var showReadiness: Bool
    @EnvironmentObject var readiness: Readiness

    var body: some View {
        HStack(spacing: 10) {
            Spacer()
            if center.phone.connected {
                if let signal = center.phone.indicators?.signal { SignalBars(level: signal) }
                if let battery = center.phone.indicators?.battchg { BatteryDot(level: battery) }
            }
            Button {
                showReadiness = true
            } label: {
                StatusChip(text: needsSetup ? "Setup needed" : statusText, tint: needsSetup ? .orange : tint)
            }
            .buttonStyle(PressButtonStyle())
            .help("Setup and status")
            CircleButton(symbol: "ellipsis", label: "Settings") { showSettings = true }
        }
        .padding(.leading, 82)  // the row is shared with the window's traffic lights
        .padding(.trailing, 12)
        .frame(height: 32)
    }

    private var needsSetup: Bool { readiness.problems > 0 && center.daemonOnline }

    private var tint: Color {
        center.phone.connected ? .green : (center.daemonOnline ? .orange : .secondary)
    }

    private var statusText: String {
        if center.phone.connected {
            if let name = center.phone.name, !name.isEmpty { return name }
            return center.phones.first(where: \.connected).map(\.display) ?? "Connected"
        }
        if !center.daemonOnline { return "Box offline" }
        return center.phones.isEmpty ? "No phone paired" : "Phone not connected"
    }
}

struct SignalBars: View {
    let level: Int  // 0..5
    var body: some View {
        HStack(alignment: .bottom, spacing: 1.5) {
            ForEach(1...4, id: \.self) { bar in
                RoundedRectangle(cornerRadius: 1)
                    .fill(bar <= level ? Color.primary.opacity(0.8) : Color.secondary.opacity(0.25))
                    .frame(width: 3, height: CGFloat(3 + bar * 2))
            }
        }
    }
}

struct BatteryDot: View {
    let level: Int  // 0..5
    var body: some View {
        Image(systemName: level <= 1 ? "battery.25" : (level <= 3 ? "battery.50" : "battery.100"))
            .font(.system(size: 13)).foregroundStyle(.secondary)
            .accessibilityLabel("Battery")
    }
}

/// Small status pill next to the wordmark: dot + who/what.
struct StatusChip: View {
    let text: String
    let tint: Color
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(tint).frame(width: 6, height: 6)
            Text(text).font(.caption.weight(.medium)).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(.quaternary.opacity(0.45), in: Capsule())
        .animation(.easeOut(duration: 0.2), value: text)
    }
}

/// Round icon button with a soft shadow instead of a border.
struct CircleButton: View {
    let symbol: String
    let label: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 26, height: 26)
                .background(.quaternary.opacity(0.45), in: Circle())
                .accessibilityHidden(true)
        }
        .buttonStyle(PressButtonStyle())
        .help(label)
        .accessibilityLabel(label)
    }
}
