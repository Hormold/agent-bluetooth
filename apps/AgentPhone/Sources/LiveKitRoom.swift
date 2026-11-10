import Foundation
import LiveKit

/// Wraps a LiveKit room: join to talk on a call, publish the mic, play the
/// phone-line audio. Remote audio renders automatically on macOS.
@MainActor
final class LiveKitRoom: ObservableObject {
    @Published var joined = false
    private(set) var joining = false  // set before any await: blocks a second join
    @Published var muted = false
    @Published var listening = true  // the call's audio plays here
    @Published var remoteLevel: Float = 0
    @Published var stats = WebRTCStats()

    private static let sampleInterval = Duration.milliseconds(250)

    private var room: Room?
    private var meter: Task<Void, Never>?

    /// `mic: false` joins as a listener (an agent's call): you hear it, the
    /// line doesn't hear you until you turn the mic on.
    func join(url: String, token: String, mic: Bool = true) async throws {
        guard !joining, !joined else { return }
        joining = true
        defer { joining = false }
        await leave()
        let newRoom = Room(roomOptions: RoomOptions(reportRemoteTrackStatistics: true))
        room = newRoom
        try await newRoom.connect(url: url, token: token)
        try await newRoom.localParticipant.setMicrophone(enabled: mic)
        joined = true
        muted = !mic
        listening = true
        startLevelMeter()
    }

    /// Native LiveKit DTMF (RFC 4733 codes), the same call a SIP-aware agent makes.
    func publishDTMF(_ digit: String) async throws {
        guard let room else { return }
        let code: Int
        switch digit {
        case "*": code = 10
        case "#": code = 11
        default: code = Int(digit) ?? 0
        }
        try await room.localParticipant.publishDtmf(code: UInt32(code), digit: digit)
    }

    /// Flips the mic. The button shows `muted`, so it only changes once the
    /// mic really did: a failed mute must not look like one.
    func toggleMute() async throws {
        guard let room else { return }
        try await room.localParticipant.setMicrophone(enabled: muted)
        muted.toggle()
    }

    /// Hear the call or not: subscribes to / drops the remote audio.
    func setListening(_ on: Bool) async throws {
        guard let room else { return }
        for participant in room.remoteParticipants.values {
            for publication in participant.audioTracks {
                try await (publication as? RemoteTrackPublication)?.set(enabled: on)
            }
        }
        listening = on
    }

    func leave() async {
        meter?.cancel()
        meter = nil
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
        meter?.cancel()
        meter = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.sampleInterval)  // cancellation ends the loop on the next check
                self?.sample()
            }
        }
    }

    /// Cumulative inbound counters at the previous sample.
    private struct Counters {
        let at: Date
        let bytes: UInt64
        let jitterBufferDelay: Double
        let jitterBufferCount: UInt64
    }

    private static let millisPerSecond = 1000.0

    private var last: Counters?

    private func sample() {
        guard let room else { return }
        let track = room.remoteParticipants.values
            .flatMap(\.audioTracks)
            .compactMap { $0.track as? RemoteAudioTrack }
            .first
        guard let statistics = track?.statistics else { return }
        var next = stats
        if let rtp = statistics.inboundRtpStream.first {
            remoteLevel = Float(rtp.audioLevel ?? 0)
            next.jitterMs = (rtp.jitter ?? 0) * Self.millisPerSecond
            let received = Double(rtp.packetsReceived ?? 0)
            let lost = Double(max(0, rtp.packetsLost ?? 0))
            next.lossPct = received + lost > 0 ? lost / (received + lost) * 100 : 0
            if let total = rtp.totalSamplesReceived, total > 0 {
                next.concealedPct = Double(rtp.concealedSamples ?? 0) / Double(total) * 100
            }
            // Bitrate and jitter-buffer delay as deltas since the previous sample:
            // the raw counters are cumulative since the call started.
            let now = Counters(
                at: Date(), bytes: rtp.bytesReceived ?? 0, jitterBufferDelay: rtp.jitterBufferDelay ?? 0,
                jitterBufferCount: rtp.jitterBufferEmittedCount ?? 0)
            if let previous = last {
                let elapsed = now.at.timeIntervalSince(previous.at)
                if elapsed > 0, now.bytes >= previous.bytes {
                    next.kbps = Double(now.bytes - previous.bytes) * 8 / elapsed / Self.millisPerSecond
                }
                if now.jitterBufferCount > previous.jitterBufferCount {
                    let emitted = Double(now.jitterBufferCount - previous.jitterBufferCount)
                    next.bufferMs =
                        (now.jitterBufferDelay - previous.jitterBufferDelay) / emitted * Self.millisPerSecond
                }
            }
            last = now
        }
        next.codec = statistics.codec.first?.mimeType?.replacingOccurrences(of: "audio/", with: "") ?? ""
        next.rttMs = (statistics.iceCandidatePair.compactMap(\.currentRoundTripTime).first ?? 0) * Self.millisPerSecond
        stats = next
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
