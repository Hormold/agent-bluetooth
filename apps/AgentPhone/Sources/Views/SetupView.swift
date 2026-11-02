import AppKit
import Combine
import SwiftUI

/// What stands between the user and a working phone, in order. The app shows
/// exactly one of these at a time and moves on by itself as soon as it's done.
enum SetupStep: Equatable {
    case startBox      // no daemon at the configured address
    case plugDongle    // daemon up, Bluetooth radio not responding
    case pairPhone     // radio up, no phone has ever paired
}

extension CallCenter {
    var setupStep: SetupStep? {
        if !daemonOnline { return .startBox }
        if phone.radio == false || phone.radio_error != nil { return .plugDongle }
        if !phone.connected && phonesLoaded && phones.isEmpty { return .pairPhone }
        return nil
    }
}

struct SetupView: View {
    @EnvironmentObject var center: CallCenter
    let step: SetupStep
    @Binding var showSettings: Bool
    private let tick = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: icon)
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(.secondary)
                .frame(width: 76, height: 76)
                .background(.quaternary.opacity(0.4), in: Circle())
                .contentTransition(.symbolEffect(.replace))
            VStack(spacing: 6) {
                Text(title).font(.title3.weight(.semibold))
                Text(detail)
                    .font(.subheadline).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 280)
            }
            if step == .startBox { CommandChip(command: "agent-bluetooth serve") }
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text(waiting).font(.caption).foregroundStyle(.tertiary)
            }
            Spacer()
            if step == .startBox {
                Button("Use another address") { showSettings = true }
                    .buttonStyle(.plain).font(.caption).foregroundStyle(.secondary)
                    .padding(.bottom, 16)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.easeOut(duration: 0.2), value: step)
        .onAppear(perform: poll)
        .onReceive(tick) { _ in poll() }
        .onChange(of: step) { _, new in if new == .pairPhone { center.pairNewPhone() } }
    }

    private func poll() {
        Task { await center.refresh() }
        center.loadDeviceState()
        if step == .pairPhone { center.pairNewPhone() }  // keep the box discoverable
    }

    private var icon: String {
        switch step {
        case .startBox: "server.rack"
        case .plugDongle: "cable.connector"
        case .pairPhone: "iphone.radiowaves.left.and.right"
        }
    }

    private var title: String {
        switch step {
        case .startBox: "Start Agent Bluetooth"
        case .plugDongle: phone_has_error ? "The Bluetooth dongle isn\u{2019}t ready" : "Plug in the Bluetooth dongle"
        case .pairPhone: "Pair your phone"
        }
    }

    private var detail: String {
        switch step {
        case .startBox: "Run this on the computer the dongle is plugged into."
        case .plugDongle: center.phone.radio_error?.hint
            ?? "The box is running but can\u{2019}t reach its USB Bluetooth adapter."
        case .pairPhone: "On your phone, open Bluetooth settings and tap \u{201C}Agent Bluetooth\u{201D}. Allow contacts and messages when asked."
        }
    }

    private var phone_has_error: Bool { center.phone.radio_error != nil }

    private var waiting: String {
        switch step {
        case .startBox: "Looking for it at \(center.host)"
        case .plugDongle: "Waiting for the dongle"
        case .pairPhone: "Visible to nearby phones"
        }
    }
}

/// A shell command you can copy with one click.
private struct CommandChip: View {
    let command: String
    @State private var copied = false

    var body: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(command, forType: .string)
            copied = true
            Task { try? await Task.sleep(nanoseconds: 1_500_000_000); copied = false }
        } label: {
            HStack(spacing: 10) {
                Text(command).font(.system(.callout, design: .monospaced))
                Image(systemName: copied ? "checkmark" : "doc.on.doc")
                    .font(.caption).foregroundStyle(.secondary)
                    .contentTransition(.symbolEffect(.replace))
            }
            .padding(.horizontal, 14).padding(.vertical, 9)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(PressButtonStyle())
        .help("Copy")
        .accessibilityLabel("Copy command \(command)")
    }
}
