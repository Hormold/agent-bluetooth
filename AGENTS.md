# Agent instructions — agent-bluetooth

Read this before changing code.

## What this is
A thin, swappable bridge: phone (Bluetooth) ⇄ events + LiveKit room + CLI.
Keep it tiny and legible. No heavy frameworks. Every capability is an adapter
behind a port in `core/ports.py`.

## Hard rules
- **No unit tests. Ever.** They are an anti-pattern here. Verify only with
  integration runs that exercise real code paths: the fake adapters against a
  live `livekit-server --dev`, or real hardware. Do not add a `tests/` dir of
  isolated unit tests, mocks of our own classes, or a pytest matrix.
- **Adapters stay decoupled.** They talk only through `EventBus` (events) and
  `AudioPipe` (PCM). No adapter imports another adapter. New hardware/protocol =
  new adapter + one line in `registry.py`, nothing else changes.
- **Policy lives in `core/router.py`**, not in adapters. Adapters report facts
  (ringing, audio up, SMS) and execute commands (answer, hangup, dial); they
  never decide what to do with a call.
- **PCM contract**: 16-bit signed LE mono. Each `AudioPipe` declares its
  `sample_rate`; handlers resample to/from it. The phone adapter owns the audio
  clock (pull == push per SCO packet).

## How to verify a change
```bash
livekit-server --dev &
uv run agent-bluetooth serve config.demo.toml &
uv run agent-bluetooth watch &                 # observe
uv run agent-bluetooth sim call number=+1555...; uv run agent-bluetooth play greeting.wav
uv run agent-bluetooth sim sms text=hi
uvx ruff check --select E,F,I,B,UP --ignore E501 src
```
Prove the event sequence on the surface the user/agent sees, then stop the
processes you started.

## Layout
- `core/` — events, bus, ports, context, router, config. No I/O specifics.
- `adapters/phone|messages|bridge|sinks|audio|bumble` — the swappable edges.
- `api/` — HTTP + SSE + web UI. `cli.py` + `guide.py` — the agent surface.
