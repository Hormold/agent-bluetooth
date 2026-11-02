import SwiftUI

struct HomeView: View {
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
                      subtitle: center.phone.connected ? "Calls you make and get show up here." : "Shows up once your phone connects.")
        } else {
            List(RecentGroup.group(center.history)) { g in
                let name = center.contactName(for: g.number) ?? (g.name == g.number ? nil : g.name)
                CallRow(icon: icon(for: g.kind), tint: tint(for: g.kind),
                        title: (name ?? PhoneFormat.pretty(g.number)) + (g.count > 1 ? " (\(g.count))" : ""),
                        subtitle: name == nil ? g.kind.capitalized : "\(g.kind.capitalized) \u{00B7} \(PhoneFormat.pretty(g.number))",
                        trailing: g.when, missed: g.kind == "missed") {
                    call(g.number)
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
                CallRow(icon: "person.fill", tint: .secondary, title: c.name,
                        subtitle: PhoneFormat.pretty(c.primary)) { call(c.primary) }
            }.listStyle(.plain).scrollContentBackground(.hidden)
        }
    }

    func call(_ n: String) { number = n; center.dial(n) }
    func icon(for kind: String) -> String {
        kind == "missed" ? "phone.down.fill" : (kind == "dialed" ? "phone.arrow.up.right" : "phone.arrow.down.left")
    }
    func tint(for kind: String) -> Color { kind == "missed" ? .red : (kind == "dialed" ? .secondary : .green) }
}

/// One row in Recents/Contacts. The whole row places the call.
struct CallRow: View {
    let icon: String, tint: Color, title: String, subtitle: String
    var trailing: String = ""
    var missed = false
    let onCall: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: onCall) {
            HStack(spacing: 10) {
                Image(systemName: icon).font(.system(size: 12)).foregroundStyle(tint).frame(width: 18)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title.isEmpty ? "Unknown" : title).font(.body.weight(.medium)).lineLimit(1)
                        .foregroundStyle(missed ? .red : .primary)
                    if !subtitle.isEmpty { Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                }
                Spacer(minLength: 8)
                ZStack(alignment: .trailing) {
                    Text(trailing).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                        .opacity(hover ? 0 : 1)
                    Image(systemName: "phone.fill").font(.caption).foregroundStyle(.green)
                        .opacity(hover ? 1 : 0)
                }
                .animation(.easeOut(duration: 0.12), value: hover)
            }
            .padding(.vertical, 4).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Call")
    }
}

/// Consecutive calls with the same number and kind collapse into one row, like a phone's Recents.
struct RecentGroup: Identifiable {
    let id: String, number: String, name: String, kind: String, count: Int, when: String

    static func group(_ items: [HistoryItem]) -> [RecentGroup] {
        var out: [RecentGroup] = []
        for h in items {
            if let last = out.last, last.number == h.number, last.kind == h.kind {
                out[out.count - 1] = RecentGroup(id: last.id, number: last.number, name: last.name,
                                                 kind: last.kind, count: last.count + 1, when: last.when)
            } else {
                out.append(RecentGroup(id: h.id, number: h.number, name: h.name, kind: h.kind,
                                       count: 1, when: PhoneFormat.when(h.at)))
            }
        }
        return out
    }
}

enum PhoneFormat {
    /// +1 949 796 9229 -> (949) 796-9229; anything else is left as is.
    static func pretty(_ number: String) -> String {
        let d = number.filter(\.isNumber)
        let ten: Substring? = d.count == 10 ? Substring(d) : (d.count == 11 && d.hasPrefix("1") ? d.dropFirst() : nil)
        guard let t = ten else { return number }
        let a = t.prefix(3), b = t.dropFirst(3).prefix(3), c = t.suffix(4)
        return "(\(a)) \(b)-\(c)"
    }

    /// vCard time (20261101T171537) -> "5:15 PM" today, "Yesterday", or "Nov 1".
    static func when(_ vcard: String) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = vcard.hasSuffix("Z") ? "yyyyMMdd'T'HHmmss'Z'" : "yyyyMMdd'T'HHmmss"
        if vcard.hasSuffix("Z") { f.timeZone = TimeZone(identifier: "UTC") }
        guard let date = f.date(from: vcard) else { return "" }
        let cal = Calendar.current
        if cal.isDateInToday(date) { return date.formatted(date: .omitted, time: .shortened) }
        if cal.isDateInYesterday(date) { return "Yesterday" }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }
}

struct EmptyHint: View {
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
