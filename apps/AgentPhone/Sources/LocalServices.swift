import Foundation

/// Installs and runs the two local services the app depends on, through
/// trusted package managers only (Homebrew, uv) and per-user LaunchAgents, so
/// they start at login and restart if they crash. Nothing is downloaded by
/// hand and nothing needs sudo.
enum LocalServices {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    static let dataDir = home.appendingPathComponent(".agent-bluetooth")
    static let configFile = dataDir.appendingPathComponent("config.toml")
    static let repo = "git+https://github.com/Hormold/agent-bluetooth"

    // MARK: finding tools

    private static let searchPath = [
        "/opt/homebrew/bin", "/usr/local/bin", home.appendingPathComponent(".local/bin").path,
        "/usr/bin", "/bin",
    ]

    static func which(_ tool: String) -> String? {
        searchPath.map { "\($0)/\(tool)" }.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static var hasBrew: Bool { which("brew") != nil }

    // MARK: running commands

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// Runs a tool and returns its combined output; throws with the tail of
    /// the output if it fails, so the UI can show something actionable.
    @discardableResult
    static func run(_ tool: String, _ args: [String]) async throws -> String {
        guard let path = which(tool) ?? (tool.hasPrefix("/") ? tool : nil) else {
            throw Failure(message: "\(tool) isn't installed")
        }
        return try await withCheckedThrowingContinuation { cont in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = (searchPath + [env["PATH"] ?? ""]).joined(separator: ":")
            env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
            p.environment = env
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            p.terminationHandler = { proc in
                let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                if proc.terminationStatus == 0 {
                    cont.resume(returning: out)
                } else {
                    let tail = out.split(separator: "\n").suffix(3).joined(separator: " ")
                    cont.resume(throwing: Failure(message: "\(tool) \(args.first ?? "") failed: \(tail)"))
                }
            }
            do { try p.run() } catch { cont.resume(throwing: error) }
        }
    }

    // MARK: LiveKit (local)

    static var liveKitInstalled: Bool { which("livekit-server") != nil }

    static func installLiveKit() async throws {
        guard hasBrew else { throw Failure(message: "Install Homebrew first (brew.sh), then try again.") }
        try await run("brew", ["install", "livekit"])
    }

    static func startLiveKit() async throws {
        guard let bin = which("livekit-server") else { throw Failure(message: "LiveKit isn't installed") }
        try await launchAgent("livekit", [bin, "--dev", "--bind", "127.0.0.1", "--node-ip", "127.0.0.1"])
    }

    /// The dev server answers plain HTTP on its signalling port.
    static func liveKitRunning(url: String = "http://127.0.0.1:7880") async -> Bool {
        guard let u = URL(string: url) else { return false }
        var req = URLRequest(url: u)
        req.timeoutInterval = 1.5
        return (try? await URLSession.shared.data(for: req)) != nil
    }

    // MARK: Agent Bluetooth daemon

    static var daemonInstalled: Bool { which("agent-bluetooth") != nil }

    static func installDaemon() async throws {
        if which("uv") == nil {
            guard hasBrew else { throw Failure(message: "Install Homebrew first (brew.sh), then try again.") }
            try await run("brew", ["install", "uv"])
        }
        try await run("uv", ["tool", "install", "--force", repo])
    }

    static func startDaemon() async throws {
        guard let bin = which("agent-bluetooth") else { throw Failure(message: "Agent Bluetooth isn't installed") }
        try writeDefaultConfigIfMissing()
        try await launchAgent("daemon", [bin, "serve", configFile.path])
    }

    /// A first config that works for most setups: first USB dongle, local
    /// LiveKit, API on this Mac only. Edit ~/.agent-bluetooth/config.toml to change.
    static func writeDefaultConfigIfMissing() throws {
        guard !FileManager.default.fileExists(atPath: configFile.path) else { return }
        try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        try """
        [bluetooth]
        transport = "usb:0+sco=2"
        name = "Agent Bluetooth"

        [phone]
        adapter = "bumble-hfp"

        [messages]
        adapter = "bumble-map"

        [directory]
        adapter = "bumble-pbap"

        [bridge]
        adapter = "livekit"
        url = "ws://127.0.0.1:7880"
        api_key = "devkey"
        api_secret = "secret"

        [routing]
        answer = "on_join"

        [http]
        host = "127.0.0.1"
        port = 8080

        [[sinks]]
        adapter = "log"

        """.write(to: configFile, atomically: true, encoding: .utf8)
    }

    // MARK: LaunchAgents

    private static func label(_ name: String) -> String { "com.agentbluetooth.\(name)" }

    private static func launchAgent(_ name: String, _ args: [String]) async throws {
        let agents = home.appendingPathComponent("Library/LaunchAgents")
        try FileManager.default.createDirectory(at: agents, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        let plist = agents.appendingPathComponent("\(label(name)).plist")
        let log = dataDir.appendingPathComponent("\(name).log").path
        let dict: [String: Any] = [
            "Label": label(name),
            "ProgramArguments": args,
            "RunAtLoad": true,
            "KeepAlive": true,
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "EnvironmentVariables": ["PATH": searchPath.joined(separator: ":")],
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: dict, format: .xml, options: 0)
        try data.write(to: plist)
        let domain = "gui/\(getuid())"
        _ = try? await run("/bin/launchctl", ["bootout", "\(domain)/\(label(name))"])  // replace an older copy
        try await run("/bin/launchctl", ["bootstrap", domain, plist.path])
    }
}
