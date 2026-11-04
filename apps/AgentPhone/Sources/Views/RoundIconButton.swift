import SwiftUI

/// Big round call-screen button: a symbol, read out as `label`.
struct RoundIconButton: View {
    let symbol: String
    let label: String
    var tint: Color = .secondary
    var filled = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 22, weight: .medium))
                .frame(width: 60, height: 60)
                .background(filled ? tint : Color.secondary.opacity(0.15), in: Circle())
                .foregroundStyle(filled ? .white : tint)
                .accessibilityHidden(true)
        }
        .buttonStyle(PressButtonStyle())
        .accessibilityLabel(label)
    }
}

/// Subtle tactile press — scale 0.96, nothing exaggerated.
struct PressButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
