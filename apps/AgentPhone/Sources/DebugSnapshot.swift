#if DEBUG
import AppKit

/// Debug builds only: with AGENTPHONE_SNAPSHOT=/path/to/file.png set, the app
/// re-renders its own window into that PNG every 2 s. Works with the screen
/// locked, which window capture tools can't do; used for docs screenshots.
enum DebugSnapshot {
    static func startIfRequested() {
        guard let path = ProcessInfo.processInfo.environment["AGENTPHONE_SNAPSHOT"] else { return }
        Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
            MainActor.assumeIsolated { write(to: path) }
        }
    }

    @MainActor private static func write(to path: String) {
        let visible = NSApp.windows.filter { $0.isVisible && $0.contentView != nil && $0.frame.width > 200 }
        let main = visible.filter { $0.styleMask.contains(.titled) }.max { lhs, rhs in
            lhs.frame.width * lhs.frame.height < rhs.frame.width * rhs.frame.height
        }
        // A sheet, when open, is what the screenshot is for.
        guard let window = visible.first(where: \.isSheet) ?? main ?? visible.first,
            let view = window.contentView?.superview ?? window.contentView
        else { return }
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        do {
            try rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        } catch {
            Log.storage.error("snapshot \(path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }
}
#endif

/// Debug builds only: AGENTPHONE_HOST overrides the box address and
/// AGENTPHONE_DEMO_JSON ({"contacts": [...], "history": [...]}) replaces the
/// phonebook, without saving either. Lets docs screenshots use fake data.
enum DemoMode {
    static var host: String? {
        #if DEBUG
        ProcessInfo.processInfo.environment["AGENTPHONE_HOST"]
        #else
        nil
        #endif
    }

    /// AGENTPHONE_OPEN=settings opens the settings sheet at launch (screenshots).
    static var openSettings: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["AGENTPHONE_OPEN"] == "settings"
        #else
        false
        #endif
    }

    static var forceGate: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["AGENTPHONE_OPEN"] == "gate"
        #else
        false
        #endif
    }

    static var openSetup: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["AGENTPHONE_OPEN"] == "setup"
        #else
        false
        #endif
    }

    static var directory: DirectorySnapshot? {
        #if DEBUG
        guard let json = ProcessInfo.processInfo.environment["AGENTPHONE_DEMO_JSON"] else { return nil }
        do {
            return try JSONDecoder().decode(DirectorySnapshot.self, from: Data(json.utf8))
        } catch {
            Log.storage.error("AGENTPHONE_DEMO_JSON: \(error.localizedDescription, privacy: .public)")
            return nil
        }
        #else
        nil
        #endif
    }
}
