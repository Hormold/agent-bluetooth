import SwiftUI

@main
struct AgentPhoneApp: App {
    @StateObject private var center = CallCenter()

    var body: some Scene {
        Window("Agent Phone", id: "main") {
            ContentView()
                .environmentObject(center)
                .onAppear {
                    #if DEBUG
                    DebugSnapshot.startIfRequested()
                    #endif
                }
                .frame(minWidth: 360, idealWidth: 380, minHeight: 540, idealHeight: 600)
        }
        .windowStyle(.hiddenTitleBar)  // the header carries the name; no duplicate title
        .windowResizability(.contentSize)
        .defaultSize(width: 380, height: 600)
    }
}
