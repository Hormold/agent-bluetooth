import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var center: CallCenter
    @Environment(\.dismiss) var dismiss

    @State private var form = SettingsForm()
    @State private var draftHost = ""
    @State private var draftKey = ""
    @State private var advanced = false
    @State private var baseline: AppSettings?  // what the box had when the form loaded

    var body: some View {
        VStack(spacing: 0) {
            SheetHeader(title: "Settings", tooltip: "Done") {
                applyBox()
                save()
                dismiss()
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    calls.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    strangers.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    recording.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    PairedPhonesSection()
                    advancedSection
                }
                .padding(.horizontal, 16).padding(.bottom, 20).padding(.top, 4)
            }
        }
        .frame(width: 400, height: 620)
        .onAppear(perform: load)
        .animation(.easeOut(duration: 0.2), value: form.mode)
        .animation(.easeOut(duration: 0.2), value: form.unknown)
        .animation(.easeOut(duration: 0.2), value: form.recordCalls)
    }

    // MARK: sections

    private var calls: some View {
        SettingsSection("When someone calls") {
            ForEach(Array(SettingsOption.modes.enumerated()), id: \.element.id) { index, option in
                RadioRow(option: option, selected: form.mode == option.id) {
                    form.mode = option.id
                    save()
                }
                if option.id == "agent", form.mode == "agent" {
                    InlineField(label: "Agent name", text: $form.agentName, placeholder: "phone-agent", onSubmit: save)
                }
                if option.id == "play", form.mode == "play" { greetingRow }
                if index < SettingsOption.modes.count - 1 { CardDivider() }
            }
        }
    }

    private var strangers: some View {
        SettingsSection("Numbers not in your contacts") {
            ForEach(Array(SettingsOption.strangers.enumerated()), id: \.element.id) { index, option in
                RadioRow(option: option, selected: form.unknown == option.id) {
                    form.unknown = option.id
                    save()
                }
                // One greeting file serves both cards; show its picker here
                // unless the card above already does.
                if option.id == "play", form.unknown == "play", form.mode != "play" { greetingRow }
                if option.id == "stall", form.unknown == "stall" {
                    ValueRow(
                        symbol: "folder", title: "Phrases",
                        value: form.stallDir.isEmpty ? "Choose folder\u{2026}" : PathText.name(form.stallDir),
                        action: chooseStallFolder)
                }
                if index < SettingsOption.strangers.count - 1 { CardDivider() }
            }
        } footer: {
            if form.unknown == "play", form.mode == "play" {
                Text("Plays the same greeting as above.")
            } else if form.unknown == "stall" {
                Text(
                    "Record a few short phrases in your own voice: "
                        + "\u{201C}Hello?\u{201D}, \u{201C}Sorry, can you repeat that?\u{201D}")
            }
        }
    }

    private var greetingRow: some View {
        ValueRow(
            symbol: "waveform", title: "Greeting",
            value: form.playFile.isEmpty ? "Choose\u{2026}" : PathText.name(form.playFile),
            action: chooseGreeting)
    }

    /// The box runs on this Mac: its folders are ours to pick and open.
    private var boxIsLocal: Bool {
        DaemonDefaults.isOnThisMac(center.host)
    }

    private var recording: some View {
        SettingsSection("Recording") {
            ToggleRow(
                symbol: "record.circle", title: "Record every call",
                detail: "Both sides, in stereo: them on the left, you or your agent on the right.",
                isOn: $form.recordCalls
            )
            .onChange(of: form.recordCalls) { save() }
            if form.recordCalls {
                CardDivider()
                if boxIsLocal {
                    ValueRow(
                        symbol: "folder", title: "Save to",
                        value: PathText.abbreviated(form.recordDir),
                        action: chooseRecordFolder)
                    Button {
                        NSWorkspace.shared.open(URL(fileURLWithPath: PathText.expanded(form.recordDir)))
                    } label: {
                        HStack(spacing: 12) {
                            Color.clear.frame(width: 20)
                            Text("Show recordings in Finder").font(.callout)
                            Spacer()
                        }
                        .foregroundStyle(Color.accentColor)
                        .padding(.horizontal, 14).padding(.bottom, 10)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                } else {
                    InlineField(label: "Folder on the box", text: $form.recordDir, mono: true, onSubmit: save)
                }
            }
        } footer: {
            if form.recordCalls {
                Text("In some places everyone on the call has to agree to be recorded.")
            }
        }
    }

    private var advancedSection: some View {
        SettingsSection("Advanced") {
            Button {
                advanced.toggle()
            } label: {
                HStack {
                    Text("Box and LiveKit")
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(advanced ? 90 : 0))
                        .accessibilityHidden(true)
                }
                .padding(.horizontal, 14).padding(.vertical, 11)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if advanced {
                CardDivider()
                InlineField(label: "Box address", text: $draftHost, mono: true, onSubmit: applyBox)
                CardDivider()
                InlineField(label: "API key", text: $draftKey, placeholder: "optional", onSubmit: applyBox)
                CardDivider()
                InlineField(label: "LiveKit URL", text: $form.liveKitURL, mono: true, onSubmit: save)
                CardDivider()
                InlineField(label: "LiveKit API key", text: $form.liveKitKey, mono: true, onSubmit: save)
                CardDivider()
                InlineField(
                    label: "LiveKit API secret", text: $form.liveKitSecret, mono: true, secure: true, onSubmit: save)
            }
        }
        .animation(.easeOut(duration: 0.2), value: advanced)
    }

    // MARK: data

    private func load() {
        draftHost = center.host
        draftKey = center.apiKey
        center.loadDeviceState()
        Task {
            guard let settings = await center.fetchSettings() else { return }
            form = SettingsForm(settings)
            baseline = settings
        }
    }

    private func chooseGreeting() {
        guard let path = FilePicker.wav(message: "Choose a 16-bit WAV to play to callers") else { return }
        form.playFile = path
        save()
    }

    private func chooseStallFolder() {
        guard let path = FilePicker.folder(message: "Choose a folder of 16-bit WAV phrases") else { return }
        form.stallDir = path
        save()
    }

    private func chooseRecordFolder() {
        let message = "Choose where call recordings are saved"
        guard let path = FilePicker.folder(message: message, startingAt: form.recordDir) else { return }
        form.recordDir = path
        save()
    }

    /// Connect to the typed address only when committed (Enter / Done).
    private func applyBox() {
        let host = draftHost.trimmingCharacters(in: .whitespaces)
        if !host.isEmpty, host != center.host { center.host = host }
        if draftKey != center.apiKey { center.apiKey = draftKey }
    }

    /// Send only what changed, and only once the box's settings are loaded,
    /// so the app never overwrites the box with stale values.
    private func save() {
        guard let settings = baseline else { return }  // never write before the box's values arrived
        let changes = form.changes(from: settings)
        guard !changes.isEmpty else { return }
        center.saveSettings(changes)
        baseline = form.applied(to: settings)
        Task { if let fresh = await center.fetchSettings() { baseline = fresh } }  // rules + summary from the box
    }
}

/// One choice in a settings card.
struct SettingsOption: Identifiable {
    static let modes = [
        Self(
            id: "app", title: "Ring in this app", detail: "You answer here. Your phone rings too.",
            symbol: "macwindow"),
        Self(
            id: "agent", title: "Hand to an AI agent", detail: "A LiveKit agent answers in the call room.",
            symbol: "sparkles"),
        Self(
            id: "play", title: "Play a greeting", detail: "Answers, plays your recording, hangs up.",
            symbol: "play.circle"),
    ]

    static let strangers = [
        Self(id: "same", title: "Same as everyone", detail: "Use the option above.", symbol: "person.2"),
        Self(
            id: "play", title: "Play a greeting", detail: "Answers, plays your recording, hangs up.",
            symbol: "play.circle"),
        Self(
            id: "stall", title: "Keep them busy", detail: "Recorded phrases between their replies, a few minutes.",
            symbol: "hourglass"),
        Self(id: "reject", title: "Decline", detail: "Hang up without answering.", symbol: "phone.down"),
    ]

    let id: String
    let title: String
    let detail: String
    let symbol: String
}

/// Paired phones, with Forget, and the way to pair another.
private struct PairedPhonesSection: View {
    @EnvironmentObject var center: CallCenter

    var body: some View {
        SettingsSection("Phone") {
            ForEach(center.phones) { paired in
                HStack(spacing: 10) {
                    Circle().fill(paired.connected ? Color.green : Color.secondary.opacity(0.4))
                        .frame(width: 7, height: 7)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(paired.display).font(.body.weight(.medium))
                        Text(paired.connected ? "Connected" : "Not connected").font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Forget") { center.forget(paired.address) }
                        .buttonStyle(.plain).font(.callout).foregroundStyle(.red)
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
                CardDivider()
            }
            Button {
                center.pairNewPhone()
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "plus").font(.system(size: 12, weight: .semibold)).frame(width: 18)
                        .accessibilityHidden(true)
                    Text(center.phones.isEmpty ? "Pair a phone" : "Pair another phone")
                    Spacer()
                }
                .foregroundStyle(Color.accentColor)
                .padding(.horizontal, 14).padding(.vertical, 11)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}
