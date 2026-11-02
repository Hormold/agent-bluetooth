import Foundation
import LiveKit

/// Wraps a LiveKit room: join to talk on a call, publish the mic, play the
/// phone-line audio. Remote audio renders automatically on macOS.
@MainActor
final class LiveKitRoom: ObservableObject {
    @Published var joined = false
    @Published var muted = false
    @Published var remoteLevel: Float = 0
    @Published var stats = WebRTCStats()

    private var room: Room?
    private var levelTimer: Timer?

    func join(url: String, token: String) async throws {
        await leave()
        let room = Room(roomOptions: RoomOptions(reportRemoteTrackStatistics: true))
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
        stats = WebRTCStats()
        last = nil
    }

    /// Level meter + WebRTC stats from the incoming phone-line track.
    /// (participant.audioLevel stays 0 here: it comes from server speaker
    /// updates, which a narrowband line never triggers. The inbound RTP stats
    /// carry the real level.)
    private func startLevelMeter() {
        levelTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.sample() }
        }
    }

    private var last: (at: Date, bytes: UInt64, jbDelay: Double, jbCount: UInt64)?

    private func sample() {
        guard let room else { return }
        let track = room.remoteParticipants.values
            .flatMap { $0.audioTracks }
            .compactMap { $0.track as? RemoteAudioTrack }
            .first
        guard let st = track?.statistics else { return }
        var s = stats
        if let rtp = st.inboundRtpStream.first {
            remoteLevel = Float(rtp.audioLevel ?? 0)
            s.jitterMs = (rtp.jitter ?? 0) * 1000
            let received = Double(rtp.packetsReceived ?? 0), lost = Double(max(0, rtp.packetsLost ?? 0))
            s.lossPct = received + lost > 0 ? lost / (received + lost) * 100 : 0
            if let total = rtp.totalSamplesReceived, total > 0 {
                s.concealedPct = Double(rtp.concealedSamples ?? 0) / Double(total) * 100
            }
            // Bitrate and jitter-buffer delay as deltas since the previous sample:
            // the raw counters are cumulative since the call started.
            let now = Date(), bytes = rtp.bytesReceived ?? 0
            let jbDelay = rtp.jitterBufferDelay ?? 0, jbCount = rtp.jitterBufferEmittedCount ?? 0
            if let p = last {
                let dt = now.timeIntervalSince(p.at)
                if dt > 0, bytes >= p.bytes { s.kbps = Double(bytes - p.bytes) * 8 / dt / 1000 }
                if jbCount > p.jbCount { s.bufferMs = (jbDelay - p.jbDelay) / Double(jbCount - p.jbCount) * 1000 }
            }
            last = (now, bytes, jbDelay, jbCount)
        }
        s.codec = st.codec.first?.mimeType?.replacingOccurrences(of: "audio/", with: "") ?? ""
        s.rttMs = (st.iceCandidatePair.compactMap { $0.currentRoundTripTime }.first ?? 0) * 1000
        stats = s
    }
}

struct WebRTCStats {
    var codec = ""
    var rttMs = 0.0
    var jitterMs = 0.0
    var bufferMs = 0.0
    var lossPct = 0.0
    var concealedPct = 0.0
    var kbps = 0.0
}
