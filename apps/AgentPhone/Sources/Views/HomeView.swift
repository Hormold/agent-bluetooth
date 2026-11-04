import SwiftUI

struct HomeView: View {
    @EnvironmentObject var center: CallCenter
    @State private var query = ""
    @State private var tab = 0  // 0 = recents, 1 = contacts, 2 = status

    var body: some View {
        VStack(spacing: 8) {
            HStack(spacing: 16) {
                ForEach(Array(["Recents", "Contacts", "Status"].enumerated()), id: \.offset) { index, title in
                    Button {
                        tab = index
                    } label: {
                        Text(title)
                            .font(.title2.weight(.bold))
                            .foregroundStyle(tab == index ? AnyShapeStyle(.primary) : AnyShapeStyle(.tertiary))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(tab == index ? .isSelected : [])
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
        if query.filter(\.isNumber).count >= 3, query.allSatisfy({ "0123456789+-() ".contains($0) }) { return digits }
        return filteredContacts.first?.primary
    }

    private func placeCall() {
        guard let number = target else { return }
        center.dial(number)
        query = ""
    }

    private func matches(_ name: String, _ number: String) -> Bool {
        let wanted = query.trimmingCharacters(in: .whitespaces).lowercased()
        if wanted.isEmpty { return true }
        let wantedDigits = wanted.filter(\.isNumber)
        return name.lowercased().contains(wanted)
            || (!wantedDigits.isEmpty && number.filter(\.isNumber).contains(wantedDigits))
    }

    private var filteredContacts: [Contact] {
        center.contacts.filter { contact in matches(contact.name, contact.numbers.joined(separator: " ")) }
    }

    // MARK: lists

    @ViewBuilder private var recentsList: some View {
        let groups = RecentGroup.group(center.history).filter { group in
            matches(center.contactName(for: group.number) ?? group.name, group.number)
        }
        if center.history.isEmpty {
            EmptyHint(
                icon: "clock.arrow.circlepath", title: "No calls yet",
                subtitle: center.phone.connected
                    ? "Calls you make and get show up here." : "Shows up once your phone connects.")
        } else if groups.isEmpty {
            EmptyHint(icon: "magnifyingglass", title: "No matches", subtitle: "Press Return to call \(query).")
        } else {
            PlainList {
                ForEach(RecentGroup.sections(groups), id: \.title) { section in
                    SectionHeader(title: section.title)
                    ForEach(Array(section.items.enumerated()), id: \.element.id) { index, group in
                        recentRow(group)
                        if index < section.items.count - 1 { RowDivider() }
                    }
                }
            }
        }
    }

    private func recentRow(_ group: RecentGroup) -> some View {
        let name = center.contactName(for: group.number) ?? (group.name == group.number ? nil : group.name)
        return InboxRow(
            icon: icon(for: group.kind), tint: tint(for: group.kind),
            meta: label(for: group.kind) + (group.count > 1 ? " \u{00D7}\(group.count)" : ""),
            metaTint: group.kind == HistoryKind.missed ? .red : .secondary,
            title: name ?? PhoneFormat.pretty(group.number),
            detail: name == nil ? "" : PhoneFormat.pretty(group.number),
            trailing: group.when
        ) { center.dial(group.number) }
    }

    @ViewBuilder private var contactsList: some View {
        let list = filteredContacts
        if center.contacts.isEmpty {
            EmptyHint(
                icon: "person.crop.circle", title: "No contacts",
                subtitle: center.directory?.syncing == "contacts"
                    ? "Syncing from your phone\u{2026}"
                    : (center.phone.connected ? "Not synced yet." : "Connect your phone first."))
        } else if list.isEmpty {
            EmptyHint(icon: "magnifyingglass", title: "No matches", subtitle: "Nobody named \u{201C}\(query)\u{201D}.")
        } else {
            PlainList {
                ForEach(Array(list.enumerated()), id: \.element.id) { index, contact in
                    InboxRow(
                        icon: nil, tint: .secondary, meta: "", metaTint: .secondary,
                        title: contact.name,
                        detail: contact.numbers.map(PhoneFormat.pretty).joined(separator: " \u{00B7} "),
                        trailing: ""
                    ) { center.dial(contact.primary) }
                    if index < list.count - 1 { RowDivider() }
                }
            }
        }
    }

    func icon(for kind: String) -> String {
        switch kind {
        case HistoryKind.missed: "phone.down.fill"
        case HistoryKind.dialed: "phone.arrow.up.right"
        default: "phone.arrow.down.left"
        }
    }

    func tint(for kind: String) -> Color {
        switch kind {
        case HistoryKind.missed: .red
        case HistoryKind.dialed: .secondary
        default: .green
        }
    }

    func label(for kind: String) -> String {
        switch kind {
        case HistoryKind.missed: "Missed"
        case HistoryKind.dialed: "Outgoing"
        default: "Incoming"
        }
    }
}
