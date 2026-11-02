import SwiftUI

/// What stands between the user and a working phone, in order. The app shows
/// exactly one of these at a time and moves on by itself as soon as it's done.
enum SetupStep: Equatable {
    case startBox      // no daemon at the configured address
    case plugDongle    // daemon up, Bluetooth radio not responding
    case pairPhone     // radio up, no phone has ever paired
}

extension CallCenter {
    var setupStep: SetupStep? {
        if !daemonOnline { return .startBox }
        if phone.radio == false || phone.radio_error != nil { return .plugDongle }
        if !phone.connected && phonesLoaded && phones.isEmpty { return .pairPhone }
        return nil
    }
}
