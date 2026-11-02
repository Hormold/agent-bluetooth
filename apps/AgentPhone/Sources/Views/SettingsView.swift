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

    private struct Mode { let id, title, detail, symbol: String }
    private let modes = [
        Mode(id: "app", title: "Ring in this app", detail: "You answer here. Your phone rings too.", symbol: "macwindow"),
        Mode(id: "agent", title: "Hand to an AI agent", detail: "A LiveKit agent answers in the call room.", symbol: "sparkles"),
        Mode(id: "play", title: "Play a greeting", detail: "Answers, plays your recording, hangs up.", symbol: "play.circle"),
        Mode(id: "record", title: "Answer and record", detail: "Answers silently and saves the audio on the box.", symbol: "record.circle"),
    ]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Settings").font(.headline)
                Spacer()
                Button("Done") { applyBox(); save(); dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 20).padding(.vertical, 14)
            Divider().opacity(0.5)
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    calls.disabled(baseline == nil)
                    phones
                    advancedSection
                }
                .padding(20)
                .overlay(alignment: .top) {
                    if baseline == nil && center.daemonOnline { ProgressView().controlSize(.small).padding(.top, 4) }
                }
            }
        }
        .frame(width: 400, height: 600)
        .onAppear(perform: load)
    }

    // MARK: sections

    private var calls: some View {
        section("When someone calls") {
            ForEach(modes, id: \.id) { m in
                ModeRow(symbol: m.symbol, title: m.title, detail: m.detail, selected: mode == m.id) {
                    mode = m.id
                    save()
                }
            }
            if mode == "agent" {
                field("Agent name", text: $agentName, placeholder: "phone-receptionist", onSubmit: save)
            }
            if mode == "play" {
                HStack(spacing: 10) {
                    Image(systemName: "waveform").foregroundStyle(.secondary)
                    Text(playFile.isEmpty ? "No recording chosen" : (playFile as NSString).lastPathComponent)
                        .lineLimit(1).truncationMode(.middle)
                        .foregroundStyle(playFile.isEmpty ? .secondary : .primary)
                    Spacer()
                    Button(playFile.isEmpty ? "Choose\u{2026}" : "Change\u{2026}", action: chooseFile)
                }
                .font(.callout)
                .padding(10)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Numbers not in your contacts").font(.callout)
                Picker("", selection: $unknown) {
                    Text("Same as above").tag("same")
                    Text("Greeting").tag("play")
                    Text("Keep busy").tag("stall")
                    Text("Decline").tag("reject")
                }
                .pickerStyle(.segmented).labelsHidden()
                .onChange(of: unknown) { _, _ in save() }
                if unknown == "play" && playFile.isEmpty {
                    Text("Choose a greeting under \u{201C}Play a greeting\u{201D} first.")
                        .font(.caption).foregroundStyle(.orange)
                }
                if unknown == "stall" {
                    HStack(spacing: 10) {
                        Image(systemName: "folder").foregroundStyle(.secondary)
                        Text(stallDir.isEmpty ? "No phrases folder" : (stallDir as NSString).lastPathComponent)
                            .lineLimit(1).truncationMode(.middle)
                            .foregroundStyle(stallDir.isEmpty ? .secondary : .primary)
                        Spacer()
                        Button(stallDir.isEmpty ? "Choose\u{2026}" : "Change\u{2026}", action: chooseStallFolder)
                    }
                    .font(.callout)
                    .padding(10)
                    .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
                    Text("Answers and plays your recorded phrases (\u{201C}Hello? Sorry, can you repeat?\u{201D}) between their replies, for a few minutes.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, 4)
            if let summary = center.settings?.summary, !summary.isEmpty {
                Text(summary).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var phones: some View {
        section("Phone") {
            ForEach(center.phones) { p in
                HStack(spacing: 8) {
                    Circle().fill(p.connected ? .green : .secondary.opacity(0.5)).frame(width: 7, height: 7)
                    Text(p.display).font(.callout)
                    Text(p.connected ? "Connected" : "Not connected").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Forget", role: .destructive) { center.forget(p.address) }
                        .buttonStyle(.borderless).font(.caption)
                }
            }
            Button { center.pairNewPhone() } label: {
                Label(center.phones.isEmpty ? "Pair a phone" : "Pair another phone", systemImage: "plus")
            }
            .buttonStyle(.borderless)
        }
    }

    private var advancedSection: some View {
        DisclosureGroup(isExpanded: $advanced) {
            VStack(alignment: .leading, spacing: 12) {
                field("Box address", text: $draftHost, mono: true, onSubmit: applyBox)
                field("API key", text: $draftKey, placeholder: "optional", onSubmit: applyBox)
                Divider().opacity(0.5)
                field("LiveKit URL", text: $lkURL, mono: true, onSubmit: save)
                field("LiveKit API key", text: $lkKey, mono: true, onSubmit: save)
                SecureField("", text: $lkSecret).textFieldStyle(.roundedBorder)
                    .font(.caption.monospaced()).onSubmit(save)
                    .overlay(alignment: .topLeading) {
                        Text("LiveKit API secret").font(.caption2).foregroundStyle(.tertiary).offset(y: -15)
                    }
                    .padding(.top, 12)
            }
            .padding(.top, 10)
        } label: {
            Text("ADVANCED").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
        }
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
            baseline?.livekit.url = lkURL; baseline?.livekit.api_key = lkKey; baseline?.livekit.api_secret = lkSecret
            Task { if let fresh = await center.fetchSettings() { baseline = fresh } }  // rules + summary from the box
        }
    }

    // MARK: building blocks

    @ViewBuilder private func section(_ title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    @ViewBuilder private func field(_ label: String, text: Binding<String>, placeholder: String = "",
                                    mono: Bool = false, onSubmit: @escaping () -> Void = {}) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .font(mono ? .caption.monospaced() : .body)
                .onSubmit(onSubmit)
        }
    }
}

struct ModeRow: View {
    let symbol: String, title: String, detail: String, selected: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: symbol).font(.system(size: 15)).frame(width: 22)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).foregroundStyle(.primary)
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary.opacity(0.4))
                    .contentTransition(.symbolEffect(.replace))
            }
            .padding(.vertical, 9).padding(.horizontal, 12)
            .background(selected ? Color.accentColor.opacity(0.10) : Color.secondary.opacity(0.05),
                        in: RoundedRectangle(cornerRadius: 10))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressButtonStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
