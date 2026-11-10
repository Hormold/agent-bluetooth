# Changelog

Versions cover the daemon, the CLI and the Agent Phone macOS app together.

## Unreleased

### Added

- **Tell the agent what to say.** `agent-bluetooth say "..."` (and the call
  screen in Agent Phone) hands the agent on the call a line; it stops and says
  it next, in its own voice. `POST /api/calls/say {text}`; the room gets a data
  message on topic `owner`; a `call.say` event follows.
- **Take over a call from the agent.** Join the call's room (Agent Phone shows
  *Take over* on an agent's call) and the example agent introduces you and goes
  quiet; leave, and it picks the call back up. `GET /api/status` now lists each
  call's `agent_name`.
- **Every call reports back.** When a call ends, the example agent posts a
  one-line outcome to `POST /api/calls/summary`, which becomes a `call.summary`
  event (`outcome`, `details`, `transcript`, `recording`, plus the call). The
  Telegram sink renders it; webhooks and automations get it like any event.
  `agent-bluetooth wait call.summary` is the way to learn how a call went.
- **One-time codes forwarded.** `agent-bluetooth otp on [--sms NUMBER |
  --webhook URL]` installs an automation that forwards the code from every
  incoming SMS (Telegram sink by default); `otp off` removes it. Automations
  can now be added at runtime: `settings.automations`, persisted with the other
  settings and validated on save.
- **Assistant button (experimental).** `agent-bluetooth assistant on` sends the
  headset's voice-recognition command (`AT+BVRA=1`, HFP 4.25) so the phone
  wakes Siri or Gemini and opens the audio link to the box. Announced in the
  hands-free features; `phone.status` events carry `assistant` when the phone
  starts or stops it. Whether the phone takes it depends on the phone and its
  lock state: a locked Pixel answered `ERROR` after 5 s (Android waited for the
  assistant app, which didn't pick up the request).
- **Phone menus and hold queues: `ivr_agent.py`.** A second example agent
  that walks a menu (keys, short spoken answers, verification codes), waits
  through hold music, redials a full queue (`ivr.sh redial`) and hands the
  call to you when a person picks up. A JSON call profile per line holds menu
  rules (walk a known menu without the model), hints, standing answers and the
  env vars for identity details, which are masked in the live log and
  transcripts. The `phone-menu-calls` skill teaches a coding agent to tune a
  new line in two or three calls.
- **The agent can call you in.** `POST /api/calls/attention {kind: person |
  question | clear, text}` becomes a `call.attention` event. Agent Phone comes
  to the front with a pulsing red frame, a sound and a banner, and shows a card
  over any screen: "A person is on the line" with a mic button, or the agent's
  question with an answer field. The answer goes to the agent as an owner line.
  `agent-bluetooth attention question "..."` raises one by hand.
- **`owner_tools.py`**: `ask_owner`, `call_owner_in` and owner-line routing as
  a mixin for any LiveKit agent. [docs/OWNER.md](docs/OWNER.md) is the
  reference for everything the app understands from an agent.
- **You hear an agent's call.** Agent Phone joins an agent's call room as a
  listener (mic off), with Sound on/off.
- **Names from call history.** A number that isn't a contact gets the name the
  phone showed for it in call history (e.g. a business caller ID), on every
  call event; `contact` stays false.

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
