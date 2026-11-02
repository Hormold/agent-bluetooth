import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject var center: CallCenter
    @Environment(\.dismiss) var dismiss

    @State private var mode = "app"
    @State private var agentName = ""
    @State private var playFile = ""
    @State private var lkURL = ""
    @State private var lkKey = ""
    @State private var lkSecret = ""
    @State private var draftHost = ""
    @State private var draftKey = ""
    @State private var advanced = false
    @State private var baseline: AppSettings?  // what the box had when the form loaded
    @State private var unknown = "same"          // numbers not in contacts: same | play | stall | reject
    @State private var stallDir = ""
    @State private var recordCalls = false
    @State private var recordDir = ""

    private struct Mode { let id, title, detail, symbol: String }
    private let modes = [
        Mode(id: "app", title: "Ring in this app", detail: "You answer here. Your phone rings too.", symbol: "macwindow"),
        Mode(id: "agent", title: "Hand to an AI agent", detail: "A LiveKit agent answers in the call room.", symbol: "sparkles"),
        Mode(id: "play", title: "Play a greeting", detail: "Answers, plays your recording, hangs up.", symbol: "play.circle"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                Text("Settings").font(.headline)
                HStack {
                    Spacer()
                    Button { applyBox(); save(); dismiss() } label: {
                        Image(systemName: "checkmark")
                            .font(.system(size: 12, weight: .bold))
                            .frame(width: 28, height: 28)
                            .background(.quaternary.opacity(0.5), in: Circle())
                    }
                    .buttonStyle(PressButtonStyle())
                    .keyboardShortcut(.defaultAction)
                    .help("Done")
                    .accessibilityLabel("Done")
                }
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 8)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    calls.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    strangers.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    recording.disabled(baseline == nil).opacity(baseline == nil ? 0.5 : 1)
                    phones
                    advancedSection
                }
                .padding(.horizontal, 16).padding(.bottom, 20).padding(.top, 4)
            }
        }
        .frame(width: 400, height: 620)
        .onAppear(perform: load)
        .animation(.easeOut(duration: 0.2), value: mode)
        .animation(.easeOut(duration: 0.2), value: unknown)
        .animation(.easeOut(duration: 0.2), value: recordCalls)
    }

    // MARK: sections

    private var calls: some View {
        SettingsSection("When someone calls") {
            ForEach(Array(modes.enumerated()), id: \.element.id) { i, m in
                RadioRow(symbol: m.symbol, title: m.title, detail: m.detail, selected: mode == m.id) {
                    mode = m.id
                    save()
                }
                if m.id == "agent" && mode == "agent" {
                    InlineField(label: "Agent name", text: $agentName, placeholder: "phone-agent", onSubmit: save)
                }
                if m.id == "play" && mode == "play" {
                    ValueRow(symbol: "waveform", title: "Greeting",
                             value: playFile.isEmpty ? "Choose\u{2026}" : (playFile as NSString).lastPathComponent,
                             action: chooseFile)
                }
                if i < modes.count - 1 { CardDivider() }
            }
        }
    }

    private struct Choice { let id, title, detail, symbol: String }
    private let strangerChoices = [
        Choice(id: "same", title: "Same as everyone", detail: "Use the option above.", symbol: "person.2"),
        Choice(id: "play", title: "Play the greeting", detail: "Answer, play it, hang up.", symbol: "play.circle"),
        Choice(id: "stall", title: "Keep them busy", detail: "Recorded phrases between their replies, a few minutes.", symbol: "hourglass"),
        Choice(id: "reject", title: "Decline", detail: "Hang up without answering.", symbol: "phone.down"),
    ]

    private var strangers: some View {
        SettingsSection("Numbers not in your contacts") {
            ForEach(Array(strangerChoices.enumerated()), id: \.element.id) { i, c in
                RadioRow(symbol: c.symbol, title: c.title, detail: c.detail, selected: unknown == c.id) {
                    unknown = c.id
                    save()
                }
                if c.id == "stall" && unknown == "stall" {
                    ValueRow(symbol: "folder", title: "Phrases",
                             value: stallDir.isEmpty ? "Choose folder\u{2026}" : (stallDir as NSString).lastPathComponent,
                             action: chooseStallFolder)
                }
                if i < strangerChoices.count - 1 { CardDivider() }
            }
        } footer: {
            if unknown == "play" && playFile.isEmpty {
                Text("Choose a greeting above first.").foregroundStyle(.orange)
            } else if unknown == "stall" {
                Text("Record a few short phrases in your own voice: \u{201C}Hello?\u{201D}, \u{201C}Sorry, can you repeat that?\u{201D}")
            }
        }
    }

    /// The box runs on this Mac: its folders are ours to pick and open.
    private var boxIsLocal: Bool {
        center.host.contains("127.0.0.1") || center.host.contains("localhost")
    }

    private var recording: some View {
        SettingsSection("Recording") {
            ToggleRow(symbol: "record.circle", title: "Record every call",
                      detail: "Both sides, in stereo: them on the left, you or your agent on the right.",
                      isOn: $recordCalls)
                .onChange(of: recordCalls) { save() }
            if recordCalls {
                CardDivider()
                if boxIsLocal {
                    ValueRow(symbol: "folder", title: "Save to",
                             value: (recordDir as NSString).abbreviatingWithTildeInPath,
                             action: chooseRecordFolder)
                    Button { NSWorkspace.shared.open(URL(fileURLWithPath: (recordDir as NSString).expandingTildeInPath)) } label: {
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
                    InlineField(label: "Folder on the box", text: $recordDir, mono: true, onSubmit: save)
                }
            }
        } footer: {
            if recordCalls {
                Text("In some places everyone on the call has to agree to be recorded.")
            }
        }
    }

    private var phones: some View {
        SettingsSection("Phone") {
            ForEach(center.phones) { p in
                HStack(spacing: 10) {
                    Circle().fill(p.connected ? Color.green : Color.secondary.opacity(0.4)).frame(width: 7, height: 7)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.display).font(.body.weight(.medium))
                        Text(p.connected ? "Connected" : "Not connected").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Forget") { center.forget(p.address) }
                        .buttonStyle(.plain).font(.callout).foregroundStyle(.red)
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
                CardDivider()
            }
            Button { center.pairNewPhone() } label: {
                HStack(spacing: 10) {
                    Image(systemName: "plus").font(.system(size: 12, weight: .semibold)).frame(width: 18)
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

    private var advancedSection: some View {
        SettingsSection("Advanced") {
            Button { advanced.toggle() } label: {
                HStack {
                    Text("Box and LiveKit")
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(advanced ? 90 : 0))
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
                InlineField(label: "LiveKit URL", text: $lkURL, mono: true, onSubmit: save)
                CardDivider()
                InlineField(label: "LiveKit API key", text: $lkKey, mono: true, onSubmit: save)
                CardDivider()
                InlineField(label: "LiveKit API secret", text: $lkSecret, mono: true, secure: true, onSubmit: save)
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
            guard let s = await center.fetchSettings() else { return }
            mode = s.incoming; agentName = s.agent_name; playFile = s.play_file
            lkURL = s.livekit.url; lkKey = s.livekit.api_key; lkSecret = s.livekit.api_secret
            stallDir = s.stall_dir ?? ""
            recordCalls = s.record_calls ?? false
            recordDir = s.record_dir ?? ""
            unknown = s.rules?.first(where: \.isUnknownNumbersRule).flatMap { $0.mode ?? $0.action } ?? "same"
            baseline = s
        }
    }

    private func chooseFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.wav]
        panel.message = "Choose a 16-bit WAV to play to callers"
        if panel.runModal() == .OK, let url = panel.url {
            playFile = url.path
            save()
        }
    }

    private func chooseStallFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "Choose a folder of 16-bit WAV phrases"
        if panel.runModal() == .OK, let url = panel.url {
            stallDir = url.path
            save()
        }
    }

    private func chooseRecordFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.directoryURL = URL(fileURLWithPath: (recordDir as NSString).expandingTildeInPath)
        panel.message = "Choose where call recordings are saved"
        if panel.runModal() == .OK, let url = panel.url {
            recordDir = url.path
            save()
        }
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
        guard let s = baseline else { return }  // never write before the box's values arrived
        var body: [String: Any] = [:]
        if mode != s.incoming {
            body["incoming"] = mode
            body["answer"] = (mode == "app" || mode == "agent") ? "on_join" : "auto"
        }
        if agentName != s.agent_name { body["agent_name"] = agentName }
        if playFile != s.play_file { body["play_file"] = playFile }
        let oldUnknown = s.rules?.first(where: \.isUnknownNumbersRule).flatMap { $0.mode ?? $0.action } ?? "same"
        if stallDir != (s.stall_dir ?? "") { body["stall_dir"] = stallDir }
        if recordCalls != (s.record_calls ?? false) { body["record_calls"] = recordCalls }
        if !recordDir.isEmpty && recordDir != (s.record_dir ?? "") { body["record_dir"] = recordDir }
        let waiting = (unknown == "play" && playFile.isEmpty) || (unknown == "stall" && stallDir.isEmpty)
        if unknown != oldUnknown && !waiting {
            var rules = (s.rules ?? []).filter { !$0.isUnknownNumbersRule }
            if unknown != "same" { rules.insert(CallRule(when_contact: false, mode: unknown), at: 0) }
            if let data = try? JSONEncoder().encode(rules), let obj = try? JSONSerialization.jsonObject(with: data) {
                body["rules"] = obj
            }
        }
        if lkURL != s.livekit.url || lkKey != s.livekit.api_key || lkSecret != s.livekit.api_secret {
            body["livekit"] = ["url": lkURL, "api_key": lkKey, "api_secret": lkSecret]
        }
        if !body.isEmpty {
            center.saveSettings(body)
            baseline?.incoming = mode; baseline?.agent_name = agentName; baseline?.play_file = playFile
            baseline?.stall_dir = stallDir
            baseline?.record_calls = recordCalls; baseline?.record_dir = recordDir
            baseline?.livekit.url = lkURL; baseline?.livekit.api_key = lkKey; baseline?.livekit.api_secret = lkSecret
            Task { if let fresh = await center.fetchSettings() { baseline = fresh } }  // rules + summary from the box
        }
    }
}

// MARK: - Grouped card building blocks (T3-style)

struct SettingsSection<Content: View, Footer: View>: View {
    let title: String
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    init(_ title: String, @ViewBuilder content: () -> Content, @ViewBuilder footer: () -> Footer) {
        self.title = title
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.leading, 14)
            VStack(spacing: 0) { content }
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 14))
            footer
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 14)
        }
    }
}

extension SettingsSection where Footer == EmptyView {
    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.init(title, content: content, footer: { EmptyView() })
    }
}

struct CardDivider: View {
    var body: some View {
        Rectangle().fill(Color.primary.opacity(0.07)).frame(height: 1).padding(.leading, 46)
    }
}

/// One option in a card; the whole row selects it.
struct RadioRow: View {
    let symbol: String, title: String, detail: String, selected: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: symbol).font(.system(size: 14)).frame(width: 20)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).foregroundStyle(.primary)
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 8)
                Image(systemName: "checkmark")
                    .font(.system(size: 12, weight: .bold)).foregroundStyle(Color.accentColor)
                    .opacity(selected ? 1 : 0)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// An on/off setting; the whole row flips it.
struct ToggleRow: View {
    let symbol: String, title: String, detail: String
    @Binding var isOn: Bool
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).font(.system(size: 14)).frame(width: 20)
                .foregroundStyle(isOn ? Color.accentColor : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).foregroundStyle(.primary)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 8)
            Toggle(title, isOn: $isOn).labelsHidden().toggleStyle(.switch).controlSize(.small)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .contentShape(Rectangle())
        .onTapGesture { isOn.toggle() }
    }
}

/// A setting that opens a picker: label on the left, current value + chevron on the right.
struct ValueRow: View {
    let symbol: String, title: String, value: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Color.clear.frame(width: 20)
                Image(systemName: symbol).font(.caption).foregroundStyle(.secondary)
                Text(title).font(.callout)
                Spacer(minLength: 8)
                Text(value).font(.callout).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 14).padding(.bottom, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Label above a borderless text field, inside a card.
struct InlineField: View {
    let label: String
    @Binding var text: String
    var placeholder = ""
    var mono = false
    var secure = false
    let onSubmit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Group {
                if secure { SecureField(placeholder, text: $text) } else { TextField(placeholder, text: $text) }
            }
            .textFieldStyle(.plain)
            .font(mono ? .callout.monospaced() : .callout)
            .onSubmit(onSubmit)
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
