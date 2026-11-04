import SwiftUI

/// Floating capsule at the bottom: type a number, or a name to search.
struct DialComposer: View {
    @Binding var text: String
    let canCall: Bool
    let onCall: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary).font(.system(size: 13))
                .accessibilityHidden(true)
            TextField("Name or number", text: $text)
                .textFieldStyle(.plain)
                .font(.body.monospacedDigit())
                .onSubmit { if canCall { onCall() } }
            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary).accessibilityHidden(true)
                }
                .buttonStyle(.plain).accessibilityLabel("Clear")
            }
            Button(action: onCall) {
                Image(systemName: "phone.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 32, height: 32)
                    .background(canCall ? Color.green : Color.secondary.opacity(0.35), in: Circle())
                    .animation(.easeOut(duration: 0.15), value: canCall)
            }
            .buttonStyle(PressButtonStyle())
            .disabled(!canCall)
            .accessibilityLabel("Call")
        }
        .padding(.leading, 16).padding(.trailing, 6).padding(.vertical, 6)
        .background(Color(nsColor: .controlBackgroundColor), in: Capsule())
        .shadow(color: .black.opacity(0.06), radius: 0.5, y: 0.5)
        .shadow(color: .black.opacity(0.10), radius: 12, y: 4)
    }
}

/// Our own list: no system separators, one hairline between rows, room at the
/// bottom for the floating composer.
struct PlainList<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) { content }
                .padding(.bottom, 72)
        }
    }
}

struct RowDivider: View {
    var body: some View {
        Rectangle().fill(.quaternary.opacity(0.7)).frame(height: 1).padding(.leading, 18)
    }
}

/// Section label between groups of rows ("Today", "Yesterday").
struct SectionHeader: View {
    let title: String
    var body: some View {
        Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary)
            .padding(.horizontal, 18).padding(.top, 14).padding(.bottom, 2)
    }
}

/// Three-line row: meta line (status · time), title, monospaced detail.
struct InboxRow: View {
    let icon: String?
    let tint: Color
    let meta: String
    let metaTint: Color
    let title: String
    let detail: String
    let trailing: String
    let onCall: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: onCall) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    if let icon {
                        Image(systemName: icon).font(.system(size: 10, weight: .semibold)).foregroundStyle(tint)
                            .accessibilityHidden(true)  // the meta text says it
                    }
                    if !meta.isEmpty { Text(meta).font(.caption).foregroundStyle(metaTint) }
                    Spacer(minLength: 8)
                    ZStack(alignment: .trailing) {
                        Text(trailing).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                            .opacity(hover ? 0 : 1)
                        Image(systemName: "phone.fill").font(.caption).foregroundStyle(.green)
                            .opacity(hover ? 1 : 0)
                            .accessibilityHidden(true)
                    }
                    .animation(.easeOut(duration: 0.12), value: hover)
                }
                .frame(height: meta.isEmpty && trailing.isEmpty && !hover ? 0 : nil)
                .opacity(meta.isEmpty && trailing.isEmpty && !hover ? 0 : 1)
                Text(title.isEmpty ? "Unknown" : title).font(.body.weight(.medium)).lineLimit(1)
                if !detail.isEmpty {
                    Text(detail).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .padding(.horizontal, 18).padding(.vertical, 9)
            .background(hover ? Color.primary.opacity(0.035) : .clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Call")
    }
}

struct EmptyHint: View {
    let icon: String, title: String, subtitle: String
    var body: some View {
        VStack(spacing: 6) {
            Spacer()
            Image(systemName: icon).font(.largeTitle).foregroundStyle(.tertiary).accessibilityHidden(true)
            Text(title).foregroundStyle(.secondary)
            Text(subtitle).font(.caption).foregroundStyle(.tertiary)
            Spacer()
        }.frame(maxWidth: .infinity)
    }
}
