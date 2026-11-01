import Foundation
import LiveKit

/// Wraps a LiveKit room: join to talk on a call, publish the mic, play the
/// phone-line audio. Remote audio renders automatically on macOS.
@MainActor
final class LiveKitRoom: ObservableObject {
    @Published var joined = false
    @Published var muted = false
    @Published var remoteLevel: Float = 0

    private var room: Room?
    private var levelTimer: Timer?

    func join(url: String, token: String) async throws {
        await leave()
        let room = Room()
        self.room = room
        try await room.connect(url: url, token: token)
        try await room.localParticipant.setMicrophone(enabled: true)
        joined = true
        muted = false
        startLevelMeter()
    }

    func toggleMute() async {
        guard let room else { return }
        muted.toggle()
        try? await room.localParticipant.setMicrophone(enabled: !muted)
    }

    func leave() async {
        levelTimer?.invalidate()
        levelTimer = nil
        if let room { await room.disconnect() }
        room = nil
        joined = false
        remoteLevel = 0
    }

    private func startLevelMeter() {
        levelTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self, let room = self.room else { return }
            let level = room.remoteParticipants.values.map { $0.audioLevel }.max() ?? 0
            Task { @MainActor in self.remoteLevel = Float(level) }
        }
    }
}
