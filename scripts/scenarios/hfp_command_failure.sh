#!/usr/bin/env bash
# Scenario: a phone command that fails is reported, never swallowed.
# One daemon on fake adapters, driven only through the real CLI + HTTP API:
#   1. `sim fail cmd=hangup` -> `hangup` exits 1 with {error, status: 502}
#   2. `sim fail cmd=hangup reason=timeout` -> `hangup` exits 1 with status 504
#   3. the failure is one-shot: the next hangup succeeds (exit 0, call.ended)
#   4. `sim fail cmd=dial` -> `call` is accepted (request_id), then call.failed
#      arrives with that request_id and reason, and no call.dialing follows
# Exit 0 = all held. Standalone: SCENARIO_PORT overrides the port (default 29180).
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR=$(mktemp -d)
PORT=${SCENARIO_PORT:-29180}
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
[bridge]
adapter = "null"
[http]
port = $PORT
[routing]
incoming = "ignore"
outgoing = "ignore"
[instance]
state_dir = "$DIR/state"
TOML

uv run --quiet agent-bluetooth serve "$CFG" >"$DIR/daemon.log" 2>&1 &
DAEMON=$!
for _ in $(seq 1 60); do curl -sf "localhost:$PORT/api/status" >/dev/null && break; sleep 0.25; done
curl -sf "localhost:$PORT/api/status" >/dev/null || fail "daemon did not come up"

# 1 + 2: hangup rejected (502) and timed out (504), each surfaced as CLI exit 1
"${AB[@]}" sim call number=+15550001111 >/dev/null
for reason in rejected timeout; do
  "${AB[@]}" sim fail cmd=hangup reason=$reason >/dev/null
  set +e; OUT=$("${AB[@]}" hangup); CODE=$?; set -e
  echo "hangup with $reason failure -> exit $CODE: $(echo "$OUT" | compact)"
  [ "$CODE" = 1 ] || fail "hangup exit code $CODE, want 1"
  want=$([ $reason = timeout ] && echo 504 || echo 502)
  [ "$(echo "$OUT" | field '["status"]')" = "$want" ] || fail "hangup status, want $want"
  echo "$OUT" | grep -q "($reason)" || fail "error text does not name the reason"
done
"${AB[@]}" status | grep -q '"state": "ringing"' || fail "failed hangup must leave the call alone"

# 3: one-shot: the next hangup goes through
"${AB[@]}" hangup >/dev/null || fail "hangup after the failure was consumed"
"${AB[@]}" status | grep -q '"call": null' || fail "call not ended by the good hangup"
echo "hangup after failure -> exit 0, call ended"

# 4: dial failure is an event correlated by request_id
"${AB[@]}" sim fail cmd=dial >/dev/null
"${AB[@]}" wait call.failed --timeout 10 >"$DIR/failed.json" &
WAITER=$!
sleep 1  # let the waiter subscribe before the dial fails
REQ=$("${AB[@]}" call +15550002222 | field '["request_id"]')
wait "$WAITER" || fail "no call.failed event"; WAITER=
echo "call -> request_id $REQ; event: $(compact <"$DIR/failed.json")"
[ "$(field '["data"]["request_id"]' <"$DIR/failed.json")" = "$REQ" ] || fail "request_id mismatch"
[ "$(field '["data"]["reason"]' <"$DIR/failed.json")" = "rejected" ] || fail "reason missing"
[ "$(field '["data"]["number"]' <"$DIR/failed.json")" = "+15550002222" ] || fail "number missing"
"${AB[@]}" status | grep -q '"type": "call.dialing"' && fail "a failed dial must not emit call.dialing"
echo "scenario ok: hfp command failures surface"
