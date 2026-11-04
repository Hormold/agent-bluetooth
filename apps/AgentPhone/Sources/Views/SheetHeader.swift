import SwiftUI

/// Title row of a sheet: centered title, round "Done" check on the right
/// (Return triggers it).
struct SheetHeader: View {
    let title: String
    var tooltip: String?
    let onDone: () -> Void

    var body: some View {
        ZStack {
            Text(title).font(.headline)
            HStack {
                Spacer()
                Button(action: onDone) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .bold))
                        .frame(width: 28, height: 28)
                        .background(.quaternary.opacity(0.5), in: Circle())
                        .accessibilityHidden(true)
                }
                .buttonStyle(PressButtonStyle())
                .keyboardShortcut(.defaultAction)
                .help(ifAny: tooltip)
                .accessibilityLabel("Done")
            }
        }
        .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 8)
    }
}

extension View {
    /// A tooltip only when there is one to show.
    @ViewBuilder
    func help(ifAny text: String?) -> some View {
        if let text { help(text) } else { self }
    }
}
