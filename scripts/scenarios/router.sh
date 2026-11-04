#!/usr/bin/env bash
# Router scenarios on fake adapters, driven through the real HTTP API: call
# lifecycle races, handler hand-over, room-open failure, per-call agents.
# Run by scripts/smoke.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR=$(mktemp -d)
PORT=$(( ${SMOKE_PORT:-29080} + 10 ))
LK_PORT=$((PORT + 1))
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill -TERM "$p" 2>/dev/null && wait "$p" 2>/dev/null; done; rm -rf "$DIR"; }
trap cleanup EXIT
fail() { echo "SCENARIO FAIL [$SCENARIO]: $*" >&2; tail -30 "$DIR"/*.log >&2; exit 1; }

api() { curl -s -X "$1" "localhost:$2$3" ${4:+-d "$4"}; }
# py <port> <expr>: evaluate a Python expression over /api/debug as `d`.
py() { api GET "$1" /api/debug | uv run --quiet python -c "import json,sys; d=json.load(sys.stdin); print($2)"; }
events() { py "$1" '" ".join(e["type"] for e in d["events"])'; }

serve() {  # serve <name> <port> <bridge toml>
  cat > "$DIR/$1.toml" <<TOML
[instance]
name = "$1"
state_dir = "$DIR/$1"
[phone]
adapter = "fake"
[bridge]
$3
[http]
port = $2
[routing]
incoming = "ignore"
TOML
  uv run --quiet agent-bluetooth serve "$DIR/$1.toml" >"$DIR/$1.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 60); do curl -sf "localhost:$2/api/status" >/dev/null && return 0; sleep 0.25; done
  SCENARIO=startup fail "$1 did not come up"
}

serve null "$PORT" 'adapter = "null"'
serve lk "$LK_PORT" $'adapter = "livekit"\nurl = "ws://127.0.0.1:1"'  # refuses every connect
uv run --quiet python -c "
import wave
with wave.open('$DIR/clip.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\x10\x00' * 8000)"

SCENARIO=ended-while-awaiting-caller-id
# A rule on the number makes routing wait for caller id; the call ends first.
api POST "$PORT" /api/settings '{"incoming":"app","rules":[{"when_number":"+1800*","mode":"reject"}]}' >/dev/null
api POST "$PORT" /api/simulate/call '{"number":""}' >/dev/null
api POST "$PORT" /api/simulate/remote_hangup >/dev/null
sleep 3.6  # past the caller-id wait
[ "$(py "$PORT" 'len(d["calls"]), len(d["rooms"])')" = "0 0" ] || fail "call or room left behind"
events "$PORT" | grep -q room.opened && fail "room opened for a call that had ended"

SCENARIO=play-then-bridge
# The clip is replaced by the room before it ends: it must stop, and its
# completion must not hang up the call that now lives in the room.
api POST "$PORT" /api/settings '{"incoming":"ignore","rules":[]}' >/dev/null
api POST "$PORT" /api/simulate/call '{"number":"+15550001111"}' >/dev/null
api POST "$PORT" /api/calls/play "{\"file\":\"$DIR/clip.wav\"}" | grep -q '"ok": true' || fail "play refused"
sleep 0.3
api POST "$PORT" /api/calls/bridge | grep -q '"ok": true' || fail "bridge refused"
sleep 2  # the 1 s clip would have ended (and hung up) by now
[ "$(py "$PORT" '[c["state"] for c in d["calls"]]')" = "['active']" ] || fail "bridged call did not survive the replaced clip"
events "$PORT" | grep -q playback.done && fail "replaced clip still reported playback.done"
api POST "$PORT" /api/simulate/remote_hangup >/dev/null
sleep 0.3
events "$PORT" | grep -q "room.opened call.ended room.closed" || fail "room not closed with the call"

SCENARIO=agent-per-call
# Two calls at once, each routed to a different agent by its own rule.
api POST "$PORT" /api/settings '{"incoming":"ignore","rules":[
  {"when_number":"+1555000111*","mode":"agent","agent_name":"alpha"},
  {"when_number":"+1555000222*","mode":"agent","agent_name":"beta"}]}' >/dev/null
api POST "$PORT" /api/simulate/call '{"number":"+15550001111"}' >/dev/null
api POST "$PORT" /api/simulate/call '{"number":"+15550002222"}' >/dev/null
sleep 0.5
got=$(py "$PORT" 'sorted((e["data"]["number"], e["data"]["agent_name"]) for e in d["events"] if e["type"] == "room.opened")[-2:]')
[ "$got" = "[('+15550001111', 'alpha'), ('+15550002222', 'beta')]" ] || fail "agents crossed between calls: $got"

SCENARIO=room-open-fails-then-retries
api POST "$LK_PORT" /api/simulate/call '{"number":"+15550003333"}' >/dev/null
for attempt in 1 2; do
  out=$(api POST "$LK_PORT" /api/calls/bridge)
  echo "$out" | grep -q "could not open a room" || fail "attempt $attempt did not report the failure: $out"
done
[ "$(py "$LK_PORT" 'len(d["rooms"])')" = "0" ] || fail "failed room kept as if open"
[ "$(grep -c "Connection refused\|could not open" "$DIR/lk.log")" -ge 2 ] || fail "second attempt did not retry the bridge"


echo "router scenarios ok"
