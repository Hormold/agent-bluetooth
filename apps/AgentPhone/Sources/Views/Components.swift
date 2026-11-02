import SwiftUI

struct CallButton: View {
    let enabled: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "phone.fill").font(.system(size: 16, weight: .semibold))
                .frame(width: 44, height: 44)
                .background(enabled ? Color.green : Color.secondary.opacity(0.3), in: Circle())
                .foregroundStyle(.white)
        }
        .buttonStyle(PressButtonStyle()).disabled(!enabled)
    }
}

struct RoundIconButton: View {
    let symbol: String
    var tint: Color = .secondary
    var filled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 22, weight: .medium))
                .frame(width: 60, height: 60)
                .background(filled ? tint : Color.secondary.opacity(0.15), in: Circle())
                .foregroundStyle(filled ? .white : tint)
        }
        .buttonStyle(PressButtonStyle())
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
