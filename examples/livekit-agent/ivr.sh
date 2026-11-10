#!/bin/zsh
# Phone menus with ivr_agent.py: one command per step.
#
#   ./ivr.sh worker profiles/x.json            start the agent (keep it running)
#   ./ivr.sh call   profiles/x.json "task"     one call
#   ./ivr.sh redial profiles/x.json "task" [minutes=10] [until=16:55]
#                                              call until a person answers
#   ./ivr.sh log                               follow the call: what we hear and do
#   ./ivr.sh answer "text"                     answer the agent's question (same as the app)
#   ./ivr.sh say "words"                       put exact words on the line
#   ./ivr.sh keys 123#                         press keys now
#
# LiveKit url/keys come from the daemon's config (~/.agent-bluetooth/config.toml)
# unless LIVEKIT_* are already set. Personal data the menu asks for (SSN, date
# of birth) is passed only as env vars named in the profile's "secrets" list;
# it is never written to the profile or the logs.
set -u
cd "${0:A:h}" || exit 1
cmd=${1:-}
LOG=calls/live.log
case $cmd in  # live controls: no profile needed
  log) exec tail -n 40 -f $LOG ;;
  answer) exec agent-bluetooth say "${2:?text}" ;;  # an open question takes it as the answer
  say) exec agent-bluetooth say "${2:?words}" ;;
  keys) exec agent-bluetooth dtmf "${2:?keys}" ;;
esac
profile=${2:-}
[ -n "$cmd" ] && [ -f "$profile" ] || { sed -n '2,16p' "$0"; exit 2; }
number=$(jq -r '.number' "$profile")

livekit_env() {
  [ -n "${LIVEKIT_API_KEY:-}" ] && return
  eval "$(uv run python -c '
import tomllib, shlex, pathlib
b = tomllib.load(open(pathlib.Path.home() / ".agent-bluetooth/config.toml", "rb"))["bridge"]
print(f"export LIVEKIT_URL={shlex.quote(b[\"url\"])} LIVEKIT_API_KEY={shlex.quote(b[\"api_key\"])} LIVEKIT_API_SECRET={shlex.quote(b[\"api_secret\"])}")
')"
}

check_secrets() {
  local missing=0 name
  for name in $(jq -r '.secrets // [] | .[]' "$profile"); do
    [ -n "${(P)name:-}" ] || { echo "missing env $name (listed in secrets)"; missing=1; }
  done
  return $missing
}

one_call() {  # prints the new log lines of this call
  local start; mkdir -p calls; touch $LOG; start=$(wc -l < $LOG)
  agent-bluetooth call "$number" --agent ivr-agent --task "$1" >/dev/null || return 1
  sleep 15
  agent-bluetooth wait call.ended --timeout 7200 >/dev/null 2>&1
  tail -n +$((start + 1)) $LOG
}

case $cmd in
  worker)
    livekit_env; check_secrets || exit 1
    CALL_PROFILE="$profile" exec uv run python ivr_agent.py ;;
  call)
    one_call "${3:?task}" ;;
  redial)
    task=${3:?task}; every=${4:-10}; until=${5:-16:55}
    for n in $(seq 1 100); do
      [ "$(date +%H:%M)" \> "$until" ] && { echo "stop: past $until"; exit 0; }
      echo "--- attempt $n $(date +%T)" >> $LOG
      out=$(one_call "$task") || { echo "attempt $n: dial failed"; sleep 30; continue; }
      if print -r -- "$out" | grep -q -E 'POSSIBLE PERSON|LIVE HUMAN'; then
        echo "attempt $n: reached a person"; exit 0
      elif print -r -- "$out" | grep -q -i -E 'more calls than we can answer|unable to assist you|try again later'; then
        echo "attempt $n: line full, next in $every min"; sleep $((every * 60))
      else
        echo "attempt $n: failed in the menu, retry in 30 s (read calls/live.log, fix the profile)"; sleep 30
      fi
    done ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
