import Foundation
import OSLog

/// Logging categories. Background refreshes log their failures here instead of
/// dropping them: `log stream --predicate 'subsystem == "com.agentbluetooth.AgentPhone"'`.
enum Log {
    private static let subsystem = Bundle.main.bundleIdentifier ?? "com.agentbluetooth.AgentPhone"
    static let api = Logger(subsystem: subsystem, category: "api")
    static let events = Logger(subsystem: subsystem, category: "events")
    static let storage = Logger(subsystem: subsystem, category: "storage")
    static let calls = Logger(subsystem: subsystem, category: "calls")
    static let setup = Logger(subsystem: subsystem, category: "setup")
}

/// A small Codable value kept as a JSON file in Application Support/AgentPhone.
/// Not UserDefaults: sandboxed builds signed for local runs silently drop
/// UserDefaults writes, so the box address kept resetting on every launch.
protocol AppSupportFile: Codable {
    static var fileName: String { get }
}

extension AppSupportFile {
    private static var url: URL? {
        guard let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        return base.appendingPathComponent("AgentPhone", isDirectory: true).appendingPathComponent(fileName)
    }

    static func loadFile() -> Self? {
        guard let url, FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            return try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        } catch {
            Log.storage.error(
                "can't read \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }

    func save() {
        guard let url = Self.url else { return }
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(self).write(to: url, options: [.atomic, .completeFileProtection])
        } catch {
            Log.storage.error(
                "can't write \(url.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }
}

/// Last directory the app saw, kept on disk so launch shows it instantly.
struct DirectorySnapshot: AppSupportFile {
    static let fileName = "directory.json"
    var contacts: [Contact]
    var history: [HistoryItem]
}

/// Box address + API key.
struct AppPrefs: AppSupportFile {
    static let fileName = "prefs.json"
    static let defaultHost = DaemonDefaults.baseURL.absoluteString  // daemon on this Mac
    var host: String = defaultHost
    var key: String = ""

    static func load() -> Self {
        guard var prefs = loadFile() else { return Self() }
        if prefs.host.trimmingCharacters(in: .whitespaces).isEmpty { prefs.host = defaultHost }
        return prefs
    }
}
