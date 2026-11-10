import SwiftUI

/// The agent needs you: a red card over whatever screen is up, and a soft red
/// pulse around the window. Laid over the content, so nothing under it moves.
struct AttentionOverlay: View {
    @EnvironmentObject var center: CallCenter
    @ObservedObject var live: LiveKitRoom
    @State private var reply = ""
    @State private var pulse = false

    var body: some View {
        if let attention = center.attention {
            ZStack(alignment: .top) {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(.red.opacity(pulse ? 0.9 : 0.35), lineWidth: 6)
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                card(attention).padding(12)
            }
            .onAppear {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { pulse = true }
            }
            .onDisappear { pulse = false }
            .transition(.opacity)
        }
    }

    private func card(_ attention: CallAttention) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(attention.title, systemImage: attention.kind == .person
                  ? "exclamationmark.triangle.fill" : "questionmark.bubble.fill")
                .font(.headline)
                .foregroundStyle(.white)
            if !attention.text.isEmpty {
                Text(attention.text).font(.callout).foregroundStyle(.white.opacity(0.9)).lineLimit(4)
            }
            switch attention.kind {
            case .person:
                Button {
                    if live.muted { center.toggleMute() } else { center.attention = nil }
                } label: {
                    Label(live.muted ? "Turn mic on and talk" : "I'm talking", systemImage: "mic.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).tint(.white).foregroundStyle(.red)
                .keyboardShortcut(.defaultAction)
            case .question:
                HStack(spacing: 8) {
                    TextField("Your answer\u{2026}", text: $reply)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit(send)
                        .accessibilityLabel("Answer for the agent")
                    Button("Answer", action: send)
                        .buttonStyle(.borderedProminent).tint(.white).foregroundStyle(.red)
                        .disabled(reply.trimmingCharacters(in: .whitespaces).isEmpty)
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.red, in: RoundedRectangle(cornerRadius: 14))
        .shadow(color: .black.opacity(0.25), radius: 12, y: 4)
    }

    private func send() {
        center.answerAgent(reply)
        reply = ""
    }
}
