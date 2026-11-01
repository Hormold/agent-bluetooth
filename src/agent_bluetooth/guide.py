"""The recipe `agent-bluetooth guide` prints. Keep it copy-pasteable."""

GUIDE = r"""
agent-bluetooth — drive a phone over Bluetooth from the command line.

MODEL
  One daemon owns the Bluetooth radio, the LiveKit bridge, and an HTTP API.
  Every CLI verb is a thin client over that API. Nothing is stateful in the CLI.
  An agent typically: (1) `watch` the event stream, (2) react to each event by
  calling a verb. Events and status are JSON; parse them, don't scrape text.

START THE DAEMON
  agent-bluetooth serve config.toml
  # no hardware? use the fake phone + fake SMS:
  agent-bluetooth serve config.demo.toml

EVENTS  (agent-bluetooth watch [families...])
  phone.connected / phone.disconnected / phone.status
  call.ringing   {call:{id,direction,number,name,state}}   incoming, not answered
  call.dialing   outgoing, not connected yet
  call.active    call is up
  call.audio     {call, up, sample_rate}                   audio link up/down
  call.ended
  room.opened    {call_id, room, url}                      a LiveKit room exists
  room.joined / room.left   {call_id, identity, others}
  sms.received   {id, sender, name, text, datetime}
  sms.sent
  playback.done  {call_id, file}

REACT TO AN INCOMING CALL  (answer=manual in config, or override any policy)
  agent-bluetooth watch call | while read -r ev; do
    type=$(jq -r .type <<<"$ev"); num=$(jq -r .data.call.number <<<"$ev")
    [ "$type" = call.ringing ] || continue
    case "$num" in
      +1800*) agent-bluetooth hangup ;;                       # block
      *)      agent-bluetooth play greeting.wav ;;            # or: bridge
    esac
  done

INCOMING-CALL POLICY WITHOUT AN AGENT  (config.toml)
  [routing]
  answer  = "on_join"     # room action: auto | on_join | manual
  default = "room"        # room | play | reject | ignore
  [[routing.rules]]
  when_number = "+1800*"
  action = "reject"
  [[routing.rules]]
  when_number = "*"
  action = "play"
  file = "greeting.wav"   # 16-bit PCM WAV, any rate (resampled to 8 kHz)

TALK TO THE CALLER YOURSELF
  agent-bluetooth bridge                 # audio -> LiveKit room
  agent-bluetooth join you               # -> {url, token}; open the web UI or an SDK
  # or point a LiveKit *agent* at every call room: set [bridge] agent_name in config.

SMS -> ANYTHING
  Forward via a sink (telegram / webhook in config), or react on the stream:
  agent-bluetooth watch sms | while read -r ev; do curl -XPOST ... ; done
  agent-bluetooth sms "+15551234567" "on my way"

SWAP HARDWARE / PROTOCOLS
  Each layer is one config line (adapter = "..."): phone, messages, bridge, sinks.
  Built in: phone = fake | bumble-hfp ; messages = fake | bumble-map ;
  bridge = livekit ; sink = log | webhook | telegram.
  Point at your own class with  adapter = "my_pkg.my_mod:MyClass".
""".strip()
