import SwiftUI

/// "Ready to take calls": one list of everything the app needs, an honest
/// status for each, and one button that sets up whatever can be done from here.
struct ReadinessView: View {
    @EnvironmentObject var center: CallCenter
    @EnvironmentObject var readiness: Readiness
    var compact = false  // inside a sheet vs. the first-run screen
    @State private var cloudURL = ""
    @State private var cloudKey = ""
    @State private var cloudSecret = ""
    @State private var wantsAgents = false
    private static let liveKitCloud = URL(literal: "https://cloud.livekit.io")

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                SettingsSection("Checklist") {
                    ForEach(Array(Readiness.Item.allCases.enumerated()), id: \.element) { index, item in
                        ReadinessRow(item: item, state: readiness.states[item]) {
                            Task { await readiness.fix(item, center) }
                        }
                        if index < Readiness.Item.allCases.count - 1 { CardDivider() }
                    }
                }
                audioChoice
            }
            .padding(.horizontal, 16).padding(.vertical, compact ? 4 : 16)
        }
        .task {
            await readiness.refresh(center)
            wantsAgents = readiness.isCloud(center)
            while !Task.isCancelled {  // re-check every 3 s while the checklist is up
                try? await Task.sleep(for: .seconds(3))  // cancellation ends the loop on the next check
                await readiness.refresh(center)
            }
        }
        .animation(.easeOut(duration: 0.2), value: readiness.states)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: readiness.problems == 0 ? "checkmark.circle.fill" : "wrench.and.screwdriver")
                .font(.system(size: 26)).foregroundStyle(readiness.problems == 0 ? .green : .orange)
                .contentTransition(.symbolEffect(.replace))
                .accessibilityHidden(true)  // the headline says it
            VStack(alignment: .leading, spacing: 2) {
                Text(readiness.problems == 0 ? "Ready to take calls" : "Almost ready").font(.title3.weight(.semibold))
                Text(
                    readiness.problems == 0
                        ? "Everything below is working."
                        : "\(readiness.problems) thing\(readiness.problems == 1 ? "" : "s") to set up."
                )
                .font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if readiness.problems > 0 {
                Button {
                    Task { await readiness.fixAll(center) }
                } label: {
                    HStack(spacing: 6) {
                        if readiness.busy { ProgressView().controlSize(.small) }
                        Text("Set up everything").font(.callout.weight(.semibold))
                    }
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Color.accentColor, in: Capsule())
                    .foregroundStyle(.white)
                }
                .buttonStyle(PressButtonStyle())
                .disabled(readiness.busy)
            }
        }
        .padding(.horizontal, 4)
    }

    private var audioChoice: some View {
        SettingsSection("How will you take calls?") {
            RadioRow(
                symbol: "person", title: "Just me, in this app",
                detail: "Free local LiveKit server on this Mac. Nothing to sign up for.",
                selected: !wantsAgents
            ) {
                wantsAgents = false
                if center.daemonOnline { center.useLocalLiveKit() }
                Task { await readiness.refresh(center) }
            }
            CardDivider()
            RadioRow(
                symbol: "sparkles", title: "With AI voice agents",
                detail: "LiveKit Cloud, where hosted voice agents run. Free tier available.",
                selected: wantsAgents
            ) { wantsAgents = true }
            if wantsAgents { cloudFields }
        }
    }

    /// LiveKit Cloud project: where to make one, and its URL and keys.
    @ViewBuilder private var cloudFields: some View {
        CardDivider()
        Link(destination: Self.liveKitCloud) {
            HStack {
                Text("Create a free project at cloud.livekit.io")
                Spacer()
                Image(systemName: "arrow.up.right").font(.caption).accessibilityHidden(true)
            }
            .font(.callout).padding(.horizontal, 14).padding(.vertical, 10)
        }
        CardDivider()
        InlineField(
            label: "Project URL", text: $cloudURL, placeholder: "wss://your-project.livekit.cloud", mono: true,
            onSubmit: saveCloud)
        CardDivider()
        InlineField(label: "API key", text: $cloudKey, mono: true, onSubmit: saveCloud)
        CardDivider()
        InlineField(label: "API secret", text: $cloudSecret, mono: true, secure: true, onSubmit: saveCloud)
        CardDivider()
        Button("Connect", action: saveCloud)
            .disabled(cloudURL.isEmpty || cloudKey.isEmpty || cloudSecret.isEmpty)
            .padding(.horizontal, 14).padding(.vertical, 10)
    }

    private func saveCloud() {
        guard !cloudURL.isEmpty, !cloudKey.isEmpty, !cloudSecret.isEmpty else { return }
        center.useCloudLiveKit(url: cloudURL, key: cloudKey, secret: cloudSecret)
        Task {
            // Let the save land first; a cancelled wait just reads early.
            try? await Task.sleep(for: .milliseconds(500))
            _ = await center.fetchSettings()
            await readiness.refresh(center)
        }
    }
}

private struct ReadinessRow: View {
    let item: Readiness.Item
    let state: Readiness.State?
    let action: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            statusIcon.frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(item.title)
                Text(detail).font(.caption).foregroundStyle(detailTint)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if case .needs(_, let title?) = state {
                Button(title, action: action).controlSize(.small)
            } else if case .failed = state {
                Button("Retry", action: action).controlSize(.small)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }

    /// Decorative: the detail line under the title says the same in words.
    @ViewBuilder private var statusIcon: some View {
        switch state {
        case .ok: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityHidden(true)
        case .working: ProgressView().controlSize(.small)
        case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(.red).accessibilityHidden(true)
        case .needs: Image(systemName: "circle.dashed").foregroundStyle(.orange).accessibilityHidden(true)
        case nil: Image(systemName: item.symbol).foregroundStyle(.tertiary).accessibilityHidden(true)
        }
    }

    private var detail: String {
        switch state {
        case .ok(let text), .working(let text), .failed(let text): text
        case .needs(let text, _): text
        case nil: "Checking\u{2026}"
        }
    }

    private var detailTint: Color {
        if case .failed = state { return .red }
        return .secondary
    }
}
