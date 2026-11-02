import SwiftUI

@main
struct AgentPhoneApp: App {
    @StateObject private var center = CallCenter()

    var body: some Scene {
        Window("Agent Phone", id: "main") {
            ContentView()
                .environmentObject(center)
                .environmentObject(center.readiness)
                .onAppear {
                    #if DEBUG
                    DebugSnapshot.startIfRequested()
                    #endif
                }
                .frame(minWidth: 360, idealWidth: 380, maxWidth: 560, minHeight: 540, idealHeight: 620, maxHeight: 1000)
        }
        .commands {
            CommandGroup(replacing: .appSettings) {
                Button("Settings\u{2026}") { center.settingsOpen = true }.keyboardShortcut(",")
            }
        }
        .windowStyle(.hiddenTitleBar)  // the header carries the name; no duplicate title
        .windowResizability(.contentMinSize)
        .defaultSize(width: 380, height: 600)
    }
}
