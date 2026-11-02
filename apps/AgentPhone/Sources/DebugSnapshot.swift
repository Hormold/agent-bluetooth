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
        guard let window = NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil }),
              let view = window.contentView?.superview ?? window.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
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

    static var directory: DirectorySnapshot? {
        #if DEBUG
        guard let json = ProcessInfo.processInfo.environment["AGENTPHONE_DEMO_JSON"] else { return nil }
        return try? JSONDecoder().decode(DirectorySnapshot.self, from: Data(json.utf8))
        #else
        nil
        #endif
    }
}
