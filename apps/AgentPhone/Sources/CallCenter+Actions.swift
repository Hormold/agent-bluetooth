import Foundation

// MARK: - Actions

extension CallCenter {
    func dial(_ number: String) {
        let trimmed = number.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        run { try await self.api.dial(trimmed) }
    }

    func answer() {
        guard let call else { return }
        run {
            let room = try await self.resolveRoom(call)
            try await self.joinRoom(room, mic: true)
        }
    }

    /// Join a LiveKit room by name (fetch a token, connect, publish mic).
    func joinRoom(_ room: String, mic: Bool) async throws {
        guard !live.joined, !live.joining else { return }
        let info = try await api.token(room: room, identity: Self.macIdentity)
        try await live.join(url: info.url, token: info.token, mic: mic)
    }

    /// In the room: native LiveKit DTMF (the box relays it to the phone, like a
    /// SIP trunk). Not in the room: straight to the box API.
    func sendDTMF(_ digit: String) {
        run {
            if self.live.joined { try await self.live.publishDTMF(digit) } else { try await self.api.dtmf(digit) }
        }
    }

    func hangup() {
        run {
            try await self.api.hangup()
            await self.live.leave()
        }
    }

    func toggleMute() {
        if live.muted, attention?.kind == .person { attention = nil }  // you're talking now
        run { try await self.live.toggleMute() }
    }

    func toggleListening() { run { try await self.live.setListening(!self.live.listening) } }

    /// Answer the agent's question: goes to the agent, not onto the line.
    func answerAgent(_ text: String) {
        say(text)
        attention = nil
    }

    /// A line for the agent on the call: it stops and says this instead.
    func say(_ text: String) {
        let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        run { try await self.api.say(line) }
    }

    func resolveRoom(_ call: Call) async throws -> String {
        if let room = call.room { return room }
        // The box opens the room a moment after the call appears: wait for it.
        for _ in 0..<10 {
            let status = try await api.status()
            if let room = status.calls.first(where: { $0.id == call.id })?.room { return room }
            try await Task.sleep(for: .milliseconds(300))
        }
        throw APIError.server(status: 0, message: "room not ready")
    }

    /// Run a user action; a failure shows up in the error banner.
    func run(_ operation: @escaping @MainActor () async throws -> Void) {
        Task {
            do {
                try await operation()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// A background refresh: failures are logged, not shown, and read as nil.
    nonisolated static func quietly<T: Sendable>(
        _ what: String, _ operation: @Sendable () async throws -> T
    ) async -> T? {
        do {
            return try await operation()
        } catch {
            Log.api.info("\(what, privacy: .public): \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }
}
