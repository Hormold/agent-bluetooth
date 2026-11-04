import Foundation

/// Consecutive calls with the same number and kind collapse into one row, like a phone's Recents.
struct RecentGroup: Identifiable {
    struct Section {
        let title: String
        let items: [RecentGroup]
    }

    let id: String, number: String, name: String, kind: String, count: Int, when: String
    var date: Date?

    static func sections(_ groups: [Self]) -> [Section] {
        let calendar = Calendar.current
        var today: [Self] = []
        var yesterday: [Self] = []
        var earlier: [Self] = []
        for group in groups {
            if let day = group.date, calendar.isDateInToday(day) {
                today.append(group)
            } else if let day = group.date, calendar.isDateInYesterday(day) {
                yesterday.append(group)
            } else {
                earlier.append(group)
            }
        }
        return [("Today", today), ("Yesterday", yesterday), ("Earlier", earlier)]
            .filter { !$0.1.isEmpty }.map { Section(title: $0.0, items: $0.1) }
    }

    static func group(_ items: [HistoryItem]) -> [Self] {
        var out: [Self] = []
        for item in items {
            if let last = out.last, last.number == item.number, last.kind == item.kind {
                out[out.count - 1] = Self(
                    id: last.id, number: last.number, name: last.name,
                    kind: last.kind, count: last.count + 1, when: last.when, date: last.date)
            } else {
                out.append(
                    Self(
                        id: item.id, number: item.number, name: item.name, kind: item.kind,
                        count: 1, when: PhoneFormat.when(item.at), date: PhoneFormat.date(item.at)))
            }
        }
        return out
    }
}

enum PhoneFormat {
    // Built once: a DateFormatter is expensive and these run for every history
    // row on every redraw.
    private static let localVCard = vCardFormatter("yyyyMMdd'T'HHmmss", timeZone: .autoupdatingCurrent)
    private static let utcVCard = vCardFormatter(
        "yyyyMMdd'T'HHmmss'Z'", timeZone: TimeZone(identifier: "UTC"))

    private static func vCardFormatter(_ format: String, timeZone: TimeZone?) -> DateFormatter {
        // A nil zone (unknown identifier) keeps the formatter's default, like before.
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = format
        if let timeZone { formatter.timeZone = timeZone }
        return formatter
    }

    /// +1 949 796 9229 -> (949) 796-9229; anything else is left as is.
    static func pretty(_ number: String) -> String {
        let digits = number.filter(\.isNumber)
        let local: Substring
        if digits.count == 10 {
            local = Substring(digits)
        } else if digits.count == 11, digits.hasPrefix("1") {
            local = digits.dropFirst()
        } else {
            return number
        }
        return "(\(local.prefix(3))) \(local.dropFirst(3).prefix(3))-\(local.suffix(4))"
    }

    static func date(_ vcard: String) -> Date? {
        (vcard.hasSuffix("Z") ? utcVCard : localVCard).date(from: vcard)
    }

    /// vCard time (20261101T171537) -> "5:15 PM" today, "Yesterday", or "Nov 1".
    static func when(_ vcard: String) -> String {
        guard let date = date(vcard) else { return "" }
        let calendar = Calendar.current
        if calendar.isDateInToday(date) { return date.formatted(date: .omitted, time: .shortened) }
        if calendar.isDateInYesterday(date) { return "Yesterday" }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }
}
