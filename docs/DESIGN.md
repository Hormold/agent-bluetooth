# Design & roadmap

What the device is, what it can expose, and what to build next. The goal: a
phone you throw in a drawer becomes a programmable endpoint an agent drives —
calls, messages, notifications, media, presence — every capability a swappable
adapter behind a port.

## Principles

- **Ports, not features.** Each capability is a Protocol in `core/ports.py`.
  Adapters implement one, talk only through `EventBus` + `AudioPipe`, and never
  import each other. New hardware/protocol = new adapter + one registry line.
- **The bus is the product.** Everything surfaces as a JSON `Event`. Agents
  `watch` the stream and react; sinks forward it; automations trigger on it.
  We never pretend to understand meaning (no built-in "this is an OTP"); we emit
  raw facts and let the agent / a user-defined rule decide.
- **Honest per-platform reality.** iPhone and Android expose different things
  over Bluetooth. We don't paper over it — we ship the adapter that works for
  each and say what doesn't.

## Platform reality (the thing that shapes everything)

| Capability | Android | iPhone | How |
| --- | --- | --- | --- |
| Calls + call audio | ✅ | ✅ | HFP (classic) |
| Caller ID / name | ✅ | ✅ | HFP +CLIP, PBAP for name |
| SMS **inbound** | ✅ MAP | ❌ MAP blocked → ✅ via ANCS | MAP (Android) / ANCS (iOS) |
| SMS **outbound** | ✅ MAP | ❌ not possible over BT | MAP (Android only) |
| App notifications (WhatsApp, OTP, banks…) | ⚠️ no clean profile | ✅ **ANCS** (all apps) | ANCS over BLE (iOS) |
| Contacts + call history | ✅ PBAP | ✅ PBAP | PBAP over OBEX |
| Media control + now-playing | ✅ AVRCP | ✅ **AMS** (BLE) | AVRCP (classic) / AMS (iOS BLE) |
| Trigger voice assistant | ✅ Google | ✅ Siri | HFP AT+BVRA, audio over SCO |
| Battery / signal / operator | ✅ | ✅ | HFP indicators / HF battery |
| Act as keyboard to phone | ✅ | ⚠️ limited | HID (classic) |

**Takeaways that drive the roadmap:**
1. iPhone SMS over Bluetooth is a dead end. The iPhone story is **ANCS** —
   which is *better* (every app, not just SMS), but read-only and over BLE, so
   the radio must also run LE and hold a second (BLE) bond next to the classic
   HFP one.
2. "Messages" is too narrow. The real abstraction is a **notification stream**;
   SMS (MAP) and iOS notifications (ANCS) are two sources feeding it.
3. We already wrote a generic OBEX client for MAP — **PBAP reuses it** for free
   (contacts + call logs → caller-name resolution).

## Port taxonomy

Shipped:
- `Phone` — calls + call audio (HFP). `fake`, `bumble-hfp`.
- `Messages` — SMS send (MAP). `fake`, `bumble-map`.
- `Bridge` / `AudioHandler` — where call audio goes. `livekit`, WAV `FilePlayer`.
- `Sink` — where events go. `log`, `webhook`, `telegram`.

Proposed (in priority order):
- `NotificationSource` — unified inbound stream `{source, app, sender, title,
  body, category, ts}`. Adapters: `bumble-map` (SMS, Android), `ancs` (iOS, all
  apps). SMS inbound folds into this; `sms.received` stays as a typed view.
- `Directory` — contacts + call history (PBAP over the existing OBEX client).
  Feeds caller-name resolution into call events; exposes `GET /api/contacts`,
  `/api/calls/history`.
- `Media` — play/pause/next/prev + now-playing. Adapters: `avrcp` (classic),
  `ams` (iOS BLE). Events `media.now_playing`; commands over API/CLI.
- `Assistant` — a `Phone` command: `assistant()` → AT+BVRA=1, route SCO to the
  bridge so an agent can talk to Siri/Google.
- `Presence` — battery / signal / operator / charging as `phone.status` events
  (partly shipped via HFP indicators; formalize + add BAS over BLE).

Cross-cutting:
- `Automations` — a config-level trigger layer (below). The agent-native
  "subscribe to X, do Y" the whole thing is for.
- `Recorder` — an `AudioHandler` that writes the call to WAV / streams to STT.
  Fits the existing audio-handler slot; no new port.

## Automation layer (the trigger system)

Today an agent must `watch` and react. Add a declarative layer for the
no-agent case, matched on any event, composable with the adapters we have:

```toml
[[automations]]
on = "sms.received"
when = { text = "*code*" }          # glob on event fields; NOT semantic parsing
do = "webhook"
url = "https://…/otp"

[[automations]]
on = "call.ringing"
when = { number = "+1800*" }
do = "hangup"

[[automations]]
on = "notification.received"
when = { app = "WhatsApp" }
do = "telegram"
```

`do` reuses sinks and phone/router commands — no new execution path. Matching is
literal/glob only; anything needing meaning is handed to an agent or an LLM the
user wires in, per the "we don't fake understanding" principle.

**Shipped** (`core/automations.py` + `core/actions.py`): rules match `on`
(event glob) + `when` (field globs), pull vars with `extract` (regex), template
action params with `{{var}}`. Actions: `log shell webhook telegram sms hangup
answer play bridge`. Verified on fake adapters: OTP extract→forward, SMS
auto-reply, call blocking. Call control stays in `[[routing.rules]]` (so the
router doesn't double-act); automations are for side-effects/notify/reply.

## Roadmap

**Now (unblocks the demo on real hardware):**
1. Hardware bring-up on the Orange Pi 3 + UB400 dongle: prove HFP calls → room
   and MAP SMS on an Android handset. (Blocked only on the dongle.)
2. MAP inbound realtime: register an MNS server so new SMS push instead of the
   5 s poll. (Nice-to-have; poll already works.)

**Next (biggest coverage win, esp. iPhone):**
3. `NotificationSource` port + `ancs` adapter → every iPhone notification
   (incl. OTP) to Telegram/webhook. Requires enabling LE on the radio and a
   BLE bond alongside HFP. This is the single highest-value addition.
4. `Directory` via PBAP (reuse `adapters/bumble/obex.py`) → caller names + call
   history.
5. `Automations` layer.

**Later (rounding out "universal controller"):**
6. `Media` (AVRCP + AMS) — control playback, now-playing events, and optionally
   route A2DP so the box is also a speaker/recorder.
7. `Assistant` (BVRA) — trigger Siri/Google into a LiveKit room.
8. `Recorder` audio handler — call recording / live transcription.
9. HID — act as a keyboard to the phone for UI automation (advanced; iOS limits).
10. Multi-phone: one box, several bonded handsets, `phone_id` on every event.

## Open questions / risks

- **ANCS bonding dance.** A proper BLE bond created during (re)pairing is
  required; community projects sometimes used a second radio. Must verify one
  CSR8510 can hold classic HFP + LE ANCS at once; fall back to a second dongle
  if not.
- **Onboard BT is UART** on the Orange Pi (and Raspberry Pi): SCO audio doesn't
  cross HCI, so call voice needs the USB dongle. Onboard could still serve LE
  (ANCS) if the USB dongle is busy with HFP — a possible two-radio split.
- **mSBC wideband** not decoded yet (CVSD 8 kHz only). Fine for a demo; add an
  mSBC decoder for better voice later.
- **Security when exposed.** API key + LAN-only by default; document a reverse
  tunnel rather than opening a port if remote access is needed.
