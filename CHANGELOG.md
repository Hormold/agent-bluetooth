# Changelog

Versions cover the daemon, the CLI and the Agent Phone macOS app together.

## 0.2.1 (2026-11-05)

### Fixed

- **Quiet, readable daemon log.** INFO now means something happened: a phone
  came or went, a call, a sync, an error. Gone: one access-log line per HTTP
  request (the app's status poll wrote ~17k a day), signal-bar ticks
  (`phone.status` is DEBUG; still on the bus and in `watch`), Bumble's feature
  dump on every reconnect and its "unhandled unsolicited response" lines, the
  LiveKit SDK's connect/disconnect echo and its false `'_data_track' closed
  unexpectedly` ERROR on every room join. `-v` brings the details back.
- **A phone out of range is logged once**, not on every 25 s page attempt
  ("phone … out of reach; still listening for it"); the next connect resets it.
- **One `phone.disconnected` per drop.** Recycling a dead link and the ACL
  going down no longer both report it; a stale link's disconnect is ignored.
- **Agent Phone:** the greeting file can be chosen right under *Numbers not in
  your contacts → Play a greeting*, not only from the top card. Status polling
  drops to every 30 s while the live event stream is up (5 s while it's down).
  New setups no longer add the `log` sink, which duplicated every event line
  in `daemon.log`; the `log` sink also skips `phone.status` unless asked.

## 0.2.0 (2026-11-04)

### Breaking

- **Phone commands report failures.** `answer`, `hangup`, `dtmf` and `volume`
  return `502` (the phone refused, or the link broke) or `504` (no reply) with
  `{error, command, reason}`. Before, they returned `{"ok": true}` no matter what.
  The CLI exits 1.
- **Dial results arrive as events.** `POST /api/calls` returns a `request_id`. If
  the phone refuses the dial, a `call.failed` event follows with the same id.
- **The HTTP API listens on `127.0.0.1` by default.** To reach a box from other
  machines, set `[http] host = "0.0.0.0"` and an `api_key`.
- **Pairing respects `seconds`.** `POST /api/pair` keeps the radio discoverable for
  that long (at most 3600) and returns `503` when the radio isn't up.
- **`[bridge] agent_name` is removed.** It never took effect. Use
  `[routing] agent_name` or a per-rule `agent_name`.

### Added

- **Several phones, one dongle each.** Add `[[instances]]` to a config, then run
  them all with `agent-bluetooth farm` or one systemd unit per phone. Each phone
  gets its own state, port and pairing name, and one dongle failing doesn't stop
  the others. `agent-bluetooth radios` lists the dongles plugged in. See
  [docs/FARM.md](docs/FARM.md).
- **Realtek firmware loads by itself** from `~/.agent-bluetooth/rtl_bt`. Before,
  RTL8761B dongles (TP-Link UB400/UB500) came up on ROM firmware, which drops
  links.

### Fixed

- An unplugged dongle no longer restarts the daemon every 40 seconds; it waits
  until the dongle is plugged in.
- Call handling:
  - a call that ends before caller ID arrives no longer leaves a room open;
  - moving a clip into a room no longer hangs up the call;
  - after a failed room open, you can try again.
- Phone commands are no longer cut off after 1 second whatever their time limit.
- Malformed Bluetooth packets in SMS and contact sync no longer hang the daemon.
- Agent Phone:
  - shows the box's error message;
  - handles `call.failed`;
  - no longer shows "offline" when the status has an unexpected field;
  - the installer no longer hangs.

### Internal

- CI checks the daemon (Ruff with every rule, ty, import boundaries, a smoke run
  of a farm on fake adapters) and the app (SwiftLint, swift-format, Swift 6
  strict concurrency). See [CONTRIBUTING.md](CONTRIBUTING.md).

## 0.1.1 (2026-11-02)

First signed and notarized Agent Phone build.

## 0.1.0 (2026-11-02)

First release.
