import AppKit
import UniformTypeIdentifiers

/// Open panels for the settings that take a file or folder on this Mac.
@MainActor
enum FilePicker {
    static func wav(message: String) -> String? {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.wav]
        panel.message = message
        return pick(panel)
    }

    /// An existing folder.
    static func folder(message: String) -> String? { pick(folderPanel(message: message)) }

    /// A folder to write into: opens at `start` and may create a new one.
    static func folder(message: String, startingAt start: String) -> String? {
        let panel = folderPanel(message: message)
        panel.canCreateDirectories = true
        panel.directoryURL = URL(fileURLWithPath: PathText.expanded(start))
        return pick(panel)
    }

    private static func folderPanel(message: String) -> NSOpenPanel {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = message
        return panel
    }

    private static func pick(_ panel: NSOpenPanel) -> String? {
        panel.runModal() == .OK ? panel.url?.path : nil
    }
}

/// Paths as people read them.
enum PathText {
    /// The last component: "greeting.wav".
    static func name(_ path: String) -> String { URL(fileURLWithPath: path).lastPathComponent }

    // swiftlint:disable legacy_objc_type - tilde handling has no Swift-native equivalent
    /// "/Users/me/Calls" -> "~/Calls".
    static func abbreviated(_ path: String) -> String { (path as NSString).abbreviatingWithTildeInPath }
    /// "~/Calls" -> "/Users/me/Calls".
    static func expanded(_ path: String) -> String { (path as NSString).expandingTildeInPath }
    // swiftlint:enable legacy_objc_type
}
