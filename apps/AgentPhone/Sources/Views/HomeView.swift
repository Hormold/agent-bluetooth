import SwiftUI

struct HomeView: View {
    @EnvironmentObject var center: CallCenter
    @State private var query = ""
    @State private var tab = 0  // 0 = recents, 1 = contacts, 2 = status

    var body: some View {
        VStack(spacing: 8) {
            HStack(spacing: 16) {
                ForEach(Array(["Recents", "Contacts", "Status"].enumerated()), id: \.offset) { i, title in
                    Button { tab = i } label: {
                        Text(title)
                            .font(.title2.weight(.bold))
                            .foregroundStyle(tab == i ? AnyShapeStyle(.primary) : AnyShapeStyle(.tertiary))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(tab == i ? .isSelected : [])
                }
                Spacer()
            }
            .padding(.horizontal, 18).padding(.top, 4)
            .animation(.easeOut(duration: 0.15), value: tab)

            Group {
                switch tab {
                case 0: recentsList
                case 1: contactsList
                default: StatusView()
                }
            }
            .frame(maxHeight: .infinity)
        }
        .safeAreaInset(edge: .bottom) {
            if tab != 2 {
                DialComposer(text: $query, canCall: center.phone.connected && target != nil) { placeCall() }
                    .padding(.horizontal, 14).padding(.bottom, 12)
                    .transition(.opacity.combined(with: .offset(y: 8)))
            }
        }
        .animation(.easeOut(duration: 0.2), value: tab)
        .onAppear { center.loadDirectory() }
        .onChange(of: tab) { _, _ in center.loadDirectory() }
        .onChange(of: center.phone.connected) { _, connected in if connected { center.loadDirectory() } }
    }

    // MARK: search + dial

    private var digits: String { query.filter { $0.isNumber || $0 == "+" } }

    /// What the call button would dial: a typed number, or the top contact match.
    private var target: String? {
        if query.trimmingCharacters(in: .whitespaces).isEmpty { return nil }
        if query.filter(\.isNumber).count >= 3 && query.allSatisfy({ "0123456789+-() ".contains($0) }) { return digits }
        return filteredContacts.first?.primary
    }

    private func placeCall() {
        guard let n = target else { return }
        center.dial(n)
        query = ""
    }

    private func matches(_ name: String, _ number: String) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        if q.isEmpty { return true }
        let qd = q.filter(\.isNumber)
        return name.lowercased().contains(q) || (!qd.isEmpty && number.filter(\.isNumber).contains(qd))
    }

    private var filteredContacts: [Contact] {
        center.contacts.filter { c in matches(c.name, c.numbers.joined(separator: " ")) }
    }

    // MARK: lists

    @ViewBuilder private var recentsList: some View {
        let groups = RecentGroup.group(center.history).filter { g in
            matches(center.contactName(for: g.number) ?? g.name, g.number)
        }
        if center.history.isEmpty {
            EmptyHint(icon: "clock.arrow.circlepath", title: "No calls yet",
                      subtitle: center.phone.connected ? "Calls you make and get show up here." : "Shows up once your phone connects.")
        } else if groups.isEmpty {
            EmptyHint(icon: "magnifyingglass", title: "No matches", subtitle: "Press Return to call \(query).")
        } else {
            PlainList {
                ForEach(RecentGroup.sections(groups), id: \.title) { section in
                    SectionHeader(title: section.title)
                    ForEach(Array(section.items.enumerated()), id: \.element.id) { i, g in
                        recentRow(g)
                        if i < section.items.count - 1 { RowDivider() }
                    }
                }
            }
        }
    }

    private func recentRow(_ g: RecentGroup) -> some View {
        let name = center.contactName(for: g.number) ?? (g.name == g.number ? nil : g.name)
        return InboxRow(
            icon: icon(for: g.kind), tint: tint(for: g.kind),
            meta: label(for: g.kind) + (g.count > 1 ? " \u{00D7}\(g.count)" : ""),
            metaTint: g.kind == "missed" ? .red : .secondary,
            title: name ?? PhoneFormat.pretty(g.number),
            detail: name == nil ? "" : PhoneFormat.pretty(g.number),
            trailing: g.when
        ) { center.dial(g.number) }
    }

    @ViewBuilder private var contactsList: some View {
        let list = filteredContacts
        if center.contacts.isEmpty {
            EmptyHint(icon: "person.crop.circle", title: "No contacts",
                      subtitle: center.directory?.syncing == "contacts" ? "Syncing from your phone\u{2026}"
                        : (center.phone.connected ? "Not synced yet." : "Connect your phone first."))
        } else if list.isEmpty {
            EmptyHint(icon: "magnifyingglass", title: "No matches", subtitle: "Nobody named \u{201C}\(query)\u{201D}.")
        } else {
            PlainList {
                ForEach(Array(list.enumerated()), id: \.element.id) { i, c in
                    InboxRow(icon: nil, tint: .secondary, meta: "", metaTint: .secondary,
                             title: c.name, detail: c.numbers.map(PhoneFormat.pretty).joined(separator: " \u{00B7} "),
                             trailing: "") { center.dial(c.primary) }
                    if i < list.count - 1 { RowDivider() }
                }
            }
        }
    }

    func icon(for kind: String) -> String {
        kind == "missed" ? "phone.down.fill" : (kind == "dialed" ? "phone.arrow.up.right" : "phone.arrow.down.left")
    }
    func tint(for kind: String) -> Color { kind == "missed" ? .red : (kind == "dialed" ? .secondary : .green) }
    func label(for kind: String) -> String {
        switch kind { case "missed": "Missed"; case "dialed": "Outgoing"; default: "Incoming" }
    }
}

/// Floating capsule at the bottom: type a number, or a name to search.
struct DialComposer: View {
    @Binding var text: String
    let canCall: Bool
    let onCall: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary).font(.system(size: 13))
            TextField("Name or number", text: $text)
                .textFieldStyle(.plain)
                .font(.body.monospacedDigit())
                .focused($focused)
                .onSubmit { if canCall { onCall() } }
            if !text.isEmpty {
                Button { text = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain).accessibilityLabel("Clear")
            }
            Button(action: onCall) {
                Image(systemName: "phone.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 32, height: 32)
                    .background(canCall ? Color.green : Color.secondary.opacity(0.35), in: Circle())
                    .animation(.easeOut(duration: 0.15), value: canCall)
            }
            .buttonStyle(PressButtonStyle())
            .disabled(!canCall)
            .accessibilityLabel("Call")
        }
        .padding(.leading, 16).padding(.trailing, 6).padding(.vertical, 6)
        .background(Color(nsColor: .controlBackgroundColor), in: Capsule())
        .shadow(color: .black.opacity(0.06), radius: 0.5, y: 0.5)
        .shadow(color: .black.opacity(0.10), radius: 12, y: 4)
    }
}

/// Our own list: no system separators, one hairline between rows, room at the
/// bottom for the floating composer.
struct PlainList<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) { content }
                .padding(.bottom, 72)
        }
    }
}

struct RowDivider: View {
    var body: some View {
        Rectangle().fill(.quaternary.opacity(0.7)).frame(height: 1).padding(.leading, 18)
    }
}

/// Section label between groups of rows ("Today", "Yesterday").
struct SectionHeader: View {
    let title: String
    var body: some View {
        Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary)
            .padding(.horizontal, 18).padding(.top, 14).padding(.bottom, 2)
    }
}

/// Three-line row: meta line (status · time), title, monospaced detail.
struct InboxRow: View {
    let icon: String?
    let tint: Color
    let meta: String
    let metaTint: Color
    let title: String
    let detail: String
    let trailing: String
    let onCall: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: onCall) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    if let icon { Image(systemName: icon).font(.system(size: 10, weight: .semibold)).foregroundStyle(tint) }
                    if !meta.isEmpty { Text(meta).font(.caption).foregroundStyle(metaTint) }
                    Spacer(minLength: 8)
                    ZStack(alignment: .trailing) {
                        Text(trailing).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                            .opacity(hover ? 0 : 1)
                        Image(systemName: "phone.fill").font(.caption).foregroundStyle(.green)
                            .opacity(hover ? 1 : 0)
                    }
                    .animation(.easeOut(duration: 0.12), value: hover)
                }
                .frame(height: meta.isEmpty && trailing.isEmpty && !hover ? 0 : nil)
                .opacity(meta.isEmpty && trailing.isEmpty && !hover ? 0 : 1)
                Text(title.isEmpty ? "Unknown" : title).font(.body.weight(.medium)).lineLimit(1)
                if !detail.isEmpty {
                    Text(detail).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .padding(.horizontal, 18).padding(.vertical, 9)
            .background(hover ? Color.primary.opacity(0.035) : .clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Call")
    }
}

/// Consecutive calls with the same number and kind collapse into one row, like a phone's Recents.
struct RecentGroup: Identifiable {
    let id: String, number: String, name: String, kind: String, count: Int, when: String
    var date: Date? = nil

    struct Section { let title: String; let items: [RecentGroup] }

    static func sections(_ groups: [RecentGroup]) -> [Section] {
        let cal = Calendar.current
        var today: [RecentGroup] = [], yesterday: [RecentGroup] = [], earlier: [RecentGroup] = []
        for g in groups {
            if let d = g.date, cal.isDateInToday(d) { today.append(g) }
            else if let d = g.date, cal.isDateInYesterday(d) { yesterday.append(g) }
            else { earlier.append(g) }
        }
        return [("Today", today), ("Yesterday", yesterday), ("Earlier", earlier)]
            .filter { !$0.1.isEmpty }.map { Section(title: $0.0, items: $0.1) }
    }

    static func group(_ items: [HistoryItem]) -> [RecentGroup] {
        var out: [RecentGroup] = []
        for h in items {
            if let last = out.last, last.number == h.number, last.kind == h.kind {
                out[out.count - 1] = RecentGroup(id: last.id, number: last.number, name: last.name,
                                                 kind: last.kind, count: last.count + 1, when: last.when, date: last.date)
            } else {
                out.append(RecentGroup(id: h.id, number: h.number, name: h.name, kind: h.kind,
                                       count: 1, when: PhoneFormat.when(h.at), date: PhoneFormat.date(h.at)))
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

    static func date(_ vcard: String) -> Date? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = vcard.hasSuffix("Z") ? "yyyyMMdd'T'HHmmss'Z'" : "yyyyMMdd'T'HHmmss"
        if vcard.hasSuffix("Z") { f.timeZone = TimeZone(identifier: "UTC") }
        return f.date(from: vcard)
    }

    /// vCard time (20261101T171537) -> "5:15 PM" today, "Yesterday", or "Nov 1".
    static func when(_ vcard: String) -> String {
        guard let date = date(vcard) else { return "" }
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
