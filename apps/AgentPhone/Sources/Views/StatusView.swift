import SwiftUI

struct StatusView: View {
    @EnvironmentObject var center: CallCenter

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
        .task {  // while the tab is up
            while !Task.isCancelled {
                center.loadStatus()
                try? await Task.sleep(for: .seconds(5))  // cancellation ends the loop on the next check
            }
        }
    }
}

struct Card<Content: View>: View {
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

struct CardTitle: View {
    let text: String
    var body: some View {
        Text(text).font(.caption.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
    }
}

struct OfflineCard: View {
    let host: String
    var body: some View {
        Card {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "wifi.exclamationmark").foregroundStyle(.orange).frame(width: 18)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Can\u{2019}t reach the box").font(.headline)
                    Text(
                        "Make sure it\u{2019}s powered on and on the same network as this Mac. "
                            + "Showing the last saved contacts and calls."
                    )
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Text(host).font(.caption.monospaced()).foregroundStyle(.tertiary).textSelection(.enabled)
                }
            }
        }
    }
}

struct PhoneCard: View {
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
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.headline)
                    HStack(spacing: 6) {
                        Circle().fill(state.1).frame(width: 7, height: 7)
                        Text(state.0).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if online, phone.connected, let indicators = phone.indicators {
                    HStack(spacing: 10) {
                        SignalBars(level: indicators.signal ?? 0)
                            .accessibilityLabel("Signal \(indicators.signal ?? 0) of 5")
                        Image(systemName: battery(indicators.battchg ?? 0)).foregroundStyle(.secondary)
                            .accessibilityLabel("Battery")
                    }
                }
            }
        }
    }

    var title: String {
        if let name = phone.name, !name.isEmpty { return name }
        guard let peer = phone.peer else { return "Phone" }
        return "Phone \u{00B7} " + String(peer.split(separator: "/").first ?? "").suffix(8)
    }

    func battery(_ level: Int) -> String {
        switch level {
        case 0: "battery.0percent"
        case 1...2: "battery.25percent"
        case 3: "battery.50percent"
        case 4: "battery.75percent"
        default: "battery.100percent"
        }
    }
}

struct SyncCard: View {
    @EnvironmentObject var center: CallCenter

    var body: some View {
        Card {
            HStack {
                CardTitle(text: "Contacts & calls")
                Spacer()
                if let syncing = center.directory?.syncing {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(syncing == "contacts" ? "Syncing contacts\u{2026}" : "Syncing calls\u{2026}")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .transition(.opacity)
                }
            }
            row("person.2", "Contacts", "contacts")
            row("clock.arrow.circlepath", "Recent calls", "history")
            if let problem = center.directory?.lastError {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.red).lineLimit(2)
            }
            Button("Sync now") { center.syncDirectory() }
                .controlSize(.small)
                .disabled(!center.phone.connected || center.directory?.syncing != nil)
        }
        .animation(.easeOut(duration: 0.2), value: center.directory?.syncing)
    }

    func row(_ icon: String, _ label: String, _ kind: String) -> some View {
        let directory = center.directory
        let count = directory?.counts[kind] ?? (kind == "contacts" ? center.contacts.count : center.history.count)
        let synced =
            directory?.syncedAt[kind].map { seconds in
                "Synced " + Date(timeIntervalSince1970: seconds).formatted(.relative(presentation: .named))
            } ?? (count > 0 ? "Saved on this Mac" : "Not synced yet")
        return HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18).accessibilityHidden(true)
            Text(label)
            Spacer()
            VStack(alignment: .trailing, spacing: 1) {
                Text("\(count)").monospacedDigit().font(.body.weight(.medium))
                Text(synced).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct BoxCard: View {
    let box: BoxInfo?
    let radio: Bool

    var body: some View {
        Card {
            CardTitle(text: "Agent Bluetooth box")
            item(
                "dot.radiowaves.left.and.right", "Bluetooth radio",
                box == nil ? "\u{2014}" : (radio ? "Ready" : "Not responding"), box != nil && !radio ? .red : nil)
            item("waveform", "Call audio", audio(box?.config.bridge), nil)
            item(
                "message", "Text messages",
                box?.config.messages == "none" || box?.config.messages == nil ? "Off" : "On", nil)
            item("clock", "Running for", box.map { uptime($0.uptimeSeconds) } ?? "\u{2014}", nil)
        }
    }

    func item(_ icon: String, _ label: String, _ value: String, _ tint: Color?) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18).accessibilityHidden(true)
            Text(label)
            Spacer()
            Text(value).foregroundStyle(tint ?? .secondary).monospacedDigit()
        }
    }

    func audio(_ bridge: String?) -> String {
        switch bridge {
        case "livekit": "LiveKit"
        case nil: "\u{2014}"
        default: "Off"
        }
    }

    func uptime(_ seconds: Int) -> String {
        let minute = 60
        let hour = 60 * minute
        let day = 24 * hour
        if seconds < minute { return "\(seconds) sec" }
        if seconds < hour { return "\(seconds / minute) min" }
        if seconds < day { return "\(seconds / hour) h \(seconds % hour / minute) min" }
        return "\(seconds / day) d \(seconds % day / hour) h"
    }
}

struct ActivityList: View {
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

struct ActivityRow: View {
    let event: Event

    static func shows(_ event: Event) -> Bool {
        ![EventType.phoneStatus, EventType.callAudio, EventType.roomClosed].contains(event.type)
    }

    var body: some View {
        let line = ActivityLine(event)
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: line.icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(line.tint)
                .frame(width: 24, height: 24)
                .background(line.tint.opacity(0.12), in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(line.title).font(.subheadline.weight(.medium))
                if !line.detail.isEmpty {
                    Text(line.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            Text(Date(timeIntervalSince1970: event.ts), format: .dateTime.hour().minute())
                .font(.caption).monospacedDigit().foregroundStyle(.tertiary)
        }
    }
}
