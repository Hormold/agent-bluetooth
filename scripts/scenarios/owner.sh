#!/usr/bin/env bash
# Scenario: the owner steps into a call and the agent reports back.
# One daemon on fake adapters, driven only through the real CLI + HTTP API:
#   1. `say` on a call that is not in a room -> exit 1 with a clear error
#   2. POST /api/calls/summary for a call that already ended -> call.summary
#      event carrying the call's number, the outcome and the transcript path
#   4. `attention question` -> call.attention event for Agent Phone; a bad kind
#      is refused; a call to a number that is only in the phone's call history
#      carries the name the phone showed (directory history)
#   3. `otp on --sms NUMBER` installs an automation; an incoming SMS with a
#      code is forwarded to that number; `otp off` removes it and nothing is
#      forwarded any more. The rule survives in settings.
# Exit 0 = all held. Standalone: SCENARIO_PORT overrides the port (default 29280).
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR=$(mktemp -d)
PORT=${SCENARIO_PORT:-29280}
CFG=$DIR/config.toml
AB=(uv run --quiet agent-bluetooth --host "http://127.0.0.1:$PORT")
cleanup() {
  [ -n "${WAITER:-}" ] && kill "$WAITER" 2>/dev/null
  [ -n "${DAEMON:-}" ] && kill -TERM "$DAEMON" 2>/dev/null && wait "$DAEMON" 2>/dev/null
  rm -rf "$DIR"
}
trap cleanup EXIT
fail() { echo "SCENARIO FAIL: $*" >&2; tail -30 "$DIR/daemon.log" >&2; exit 1; }
field() { uv run --quiet python -c "import json,sys; print(json.load(sys.stdin)$1)"; }
compact() { uv run --quiet python -c "import json,sys; print(json.dumps(json.load(sys.stdin)))"; }

cat > "$CFG" <<TOML
[phone]
adapter = "fake"
[messages]
adapter = "fake"
[bridge]
adapter = "null"
[directory]
adapter = "fake"
history = [{ name = "Acme Support", number = "+18005550123", kind = "dialed" }]
[http]
port = $PORT
[routing]
incoming = "ignore"
[instance]
state_dir = "$DIR/state"
TOML

uv run --quiet agent-bluetooth serve "$CFG" >"$DIR/daemon.log" 2>&1 &
DAEMON=$!
for _ in $(seq 1 60); do curl -sf "localhost:$PORT/api/status" >/dev/null && break; sleep 0.25; done
curl -sf "localhost:$PORT/api/status" >/dev/null || fail "daemon did not come up"

# 1: say needs a room
"${AB[@]}" sim call number=+15550001111 >/dev/null
set +e; OUT=$("${AB[@]}" say "Make it 8 instead"); CODE=$?; set -e
echo "say outside a room -> exit $CODE: $(echo "$OUT" | compact)"
[ "$CODE" = 1 ] || fail "say exit code $CODE, want 1"
echo "$OUT" | grep -q "not in a room" || fail "say error does not say why"
CALL=$("${AB[@]}" status | field '["calls"][0]["id"]')
"${AB[@]}" hangup >/dev/null

# 2: the agent reports on the call after it ended
"${AB[@]}" wait call.summary --timeout 10 >"$DIR/summary.json" &
WAITER=$!
sleep 1
curl -sf -X POST "localhost:$PORT/api/calls/summary" -H 'content-type: application/json' \
  -d "{\"call_id\": \"$CALL\", \"outcome\": \"Booked for 8pm, patio.\", \"transcript\": \"/tmp/x.json\"}" >/dev/null \
  || fail "summary for an ended call was refused"
wait "$WAITER" || fail "no call.summary event"; WAITER=
echo "summary event: $(compact <"$DIR/summary.json")"
[ "$(field '["data"]["number"]' <"$DIR/summary.json")" = "+15550001111" ] || fail "summary lost the number"
[ "$(field '["data"]["outcome"]' <"$DIR/summary.json")" = "Booked for 8pm, patio." ] || fail "outcome missing"
[ "$(field '["data"]["transcript"]' <"$DIR/summary.json")" = "/tmp/x.json" ] || fail "transcript missing"
curl -s -X POST "localhost:$PORT/api/calls/summary" -H 'content-type: application/json' \
  -d '{"call_id": "nope", "outcome": "x"}' | grep -q "no such call" || fail "unknown call must be a 404"

# 3: one-time codes forwarded by SMS, then switched off
"${AB[@]}" otp on --sms +15550009999 | grep -q '"do": "sms"' || fail "otp on did not install the rule"
"${AB[@]}" settings | grep -q '"name": "otp"' || fail "otp rule not in settings"
"${AB[@]}" sim sms sender=+12025550123 text="Your Acme code is 424242" >/dev/null
sleep 1
"${AB[@]}" sms list | grep -q '"to": "+15550009999"' || fail "code was not forwarded"
"${AB[@]}" sms list | grep -q "424242 from +12025550123" || fail "forwarded text lacks code/sender"
echo "otp on: code 424242 forwarded to +15550009999"
"${AB[@]}" otp off | grep -q '"otp": \[\]' || fail "otp off left the rule"
"${AB[@]}" sim sms sender=+12025550123 text="Your Acme code is 777777" >/dev/null
sleep 1
"${AB[@]}" sms list | grep -q "777777 from" && fail "code forwarded after otp off"
set +e; OUT=$("${AB[@]}" otp on); CODE=$?; set -e
[ "$CODE" = 1 ] && echo "$OUT" | grep -q telegram || fail "otp on without a telegram sink must explain"

# 4: the agent calls the owner in; names come from call history
"${AB[@]}" wait call.attention --timeout 10 >"$DIR/attention.json" &
WAITER=$!
sleep 1
"${AB[@]}" attention question "What ZIP code is on the account?" >/dev/null || fail "attention refused"
wait "$WAITER" || fail "no call.attention event"; WAITER=
echo "attention event: $(compact <"$DIR/attention.json")"
[ "$(field '["data"]["kind"]' <"$DIR/attention.json")" = "question" ] || fail "attention kind lost"
[ "$(field '["data"]["text"]' <"$DIR/attention.json")" = "What ZIP code is on the account?" ] || fail "attention text lost"
curl -s -X POST "localhost:$PORT/api/calls/attention" -H 'content-type: application/json' \
  -d '{"kind": "panic"}' | grep -q "person, question or clear" || fail "a bad attention kind must be refused"
"${AB[@]}" wait call.ringing --timeout 10 >"$DIR/ring.json" &
WAITER=$!
sleep 1
"${AB[@]}" sim call number=+18005550123 >/dev/null
wait "$WAITER" || fail "no call.ringing event"; WAITER=
[ "$(field '["data"]["call"]["name"]' <"$DIR/ring.json")" = "Acme Support" ] || fail "history name missing from the event"
[ "$(field '["data"]["call"]["contact"]' <"$DIR/ring.json")" = "False" ] || fail "a history name must not make a contact"
echo "history name on the event: Acme Support"
"${AB[@]}" hangup >/dev/null
echo "scenario ok: say, summary, otp, attention, history name"
