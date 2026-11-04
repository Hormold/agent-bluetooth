#!/usr/bin/env bash
# Integration smoke: a 2-instance farm on fake adapters, driven through the real
# CLI and HTTP API. Proves instance isolation (separate ports, state, events)
# and that the supervisor restarts only the instance that died. Then the
# scenarios in scripts/scenarios/.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(mktemp -d)
PORT=${SMOKE_PORT:-29080}
CFG=$DIR/farm.toml
AB=(uv run --quiet agent-bluetooth -c "$CFG")
cleanup() { [ -n "${FARM:-}" ] && kill -TERM "$FARM" 2>/dev/null && wait "$FARM" 2>/dev/null; rm -rf "$DIR"; }
trap cleanup EXIT
fail() { echo "SMOKE FAIL: $*" >&2; tail -40 "$DIR/farm.log" >&2; exit 1; }

cat > "$CFG" <<TOML
[phone]
adapter = "fake"
[messages]
adapter = "fake"
[directory]
adapter = "fake"
contacts = [{ name = "Anna Petrova", numbers = ["+14155550142"] }]
[bridge]
adapter = "null"
[http]
port = $PORT
[routing]
incoming = "ignore"
[[routing.rules]]
when_number = "+1800*"
mode = "reject"
[[instances]]
name = "one"
state_dir = "$DIR/one"
[[instances]]
name = "two"
state_dir = "$DIR/two"
TOML

uv run --quiet agent-bluetooth farm "$CFG" >"$DIR/farm.log" 2>&1 &
FARM=$!
up() { for _ in $(seq 1 60); do curl -sf "localhost:$1/api/status" >/dev/null && return 0; sleep 0.25; done; return 1; }
up "$PORT" && up $((PORT + 1)) || fail "instances did not come up"

"${AB[@]}" -i one sim call number=+18005550000 >/dev/null
"${AB[@]}" -i two sim sms text="code 424242" >/dev/null
sleep 1
"${AB[@]}" -i one status | grep -q '"state": "ended"' || fail "rule did not reject +1800 on one"
"${AB[@]}" -i two sms list | grep -q 424242 || fail "sms not delivered on two"
"${AB[@]}" -i one sms list | grep -q 424242 && fail "sms leaked from two to one"
"${AB[@]}" -i two sms list | grep -q '"instance": "two"' || fail "events are not tagged with their instance"

ONE=$(cat "$DIR/one/daemon.pid"); TWO=$(cat "$DIR/two/daemon.pid")
kill -9 "$TWO"
for _ in $(seq 1 40); do NEW=$(cat "$DIR/two/daemon.pid" 2>/dev/null || true); [ -n "$NEW" ] && [ "$NEW" != "$TWO" ] && break; sleep 0.25; done
[ "$NEW" != "$TWO" ] || fail "two was not restarted"
[ "$(cat "$DIR/one/daemon.pid")" = "$ONE" ] || fail "one restarted although only two died"
up $((PORT + 1)) || fail "two did not come back"

kill -TERM "$FARM"; wait "$FARM" || fail "farm exited non-zero"; FARM=
pgrep -f "agent_bluetooth serve $CFG" >/dev/null && fail "children outlived the farm"
scripts/scenarios/router.sh
SCENARIO_PORT=$((PORT + 100)) scripts/scenarios/hfp_command_failure.sh
uv run --quiet python scripts/scenarios/obex_malformed.py
echo "smoke ok"
