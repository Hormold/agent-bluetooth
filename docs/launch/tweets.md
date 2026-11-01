# Launch — tweet thread

**1/ (hook)**
I turned an old phone into an API.

Drop it in a drawer, pair a cheap Linux box to it over Bluetooth, and your real
number becomes programmable — calls and texts. No Twilio, no SIP, no second line.

Weekend project, open source. 🧵

**2/ (the magic)**
Every incoming call becomes a LiveKit room.

Join from your laptop (abroad, no roaming), or hand it to an AI voice agent that
answers on *your* number — it gets the caller ID in the dispatch metadata.
Outbound calls too. Same for SMS and 2FA codes → forward anywhere.

**3/ (agent-native)**
One CLI, built for agents (same spirit as @vercel's agent-browser):

  agent-bluetooth watch        # live events, one JSON per line
  agent-bluetooth call <num>
  agent-bluetooth bridge       # answer → LiveKit room

Pure Python (Google's Bumble — no BlueZ). Every layer is a swappable adapter.

**4/ (what I use it for)**
• answer my number from my laptop
• 2FA codes → Telegram
• an AI agent screens spam calls
• auto-reply “driving, call you back”

All config, no code:
  on sms.received, extract (\d{4,8}) → telegram

**5/ (credit + repo)**
Inspired by @bobuk's idea of a phone as a universal Bluetooth-attached controller.
This is a small, hackable build of it.

Rough edges — use a genuine CSR8510 dongle. Code 👇
github.com/Hormold/agent-bluetooth

---

## Standalone (single tweet)

Turned an old phone into an API over Bluetooth 📞

Calls + SMS on your *real* number, no Twilio. Incoming calls open a LiveKit room
you or an AI agent can answer. One CLI, pure Python, open source.

Weekend project 👇
