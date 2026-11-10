# Your agent and you, during a call

Reference for anyone building a call agent on agent-bluetooth (people and
coding agents alike). It lists every way the agent on a call and its owner
reach each other, what the Mac app [Agent Phone](../README.md#mac-app) does
with each one, and the wire format, so a new agent gets the same features
without reading the app's code.

The two example agents use all of it:
[agent.py](../examples/livekit-agent/agent.py) (a conversation) and
[ivr_agent.py](../examples/livekit-agent/ivr_agent.py) (phone menus, hold
queues, handoff to you).

## At a glance

| You want | The agent does | You see / do in Agent Phone |
| --- | --- | --- |
| Hear the call | nothing | The app joins the call's room as a listener (mic off). **Sound on/off** mutes it for you only. |
| Ask you something mid-call | `POST /api/calls/attention {"kind": "question", "text": "..."}` | The window comes to the front, a red frame pulses, a sound plays, a banner shows, and a question card with an answer field appears. Your answer goes to the agent, not onto the line. |
| Call you in (a person picked up) | `POST /api/calls/attention {"kind": "person", "text": "<what they said>"}` | Same alert with **A person is on the line** and **Turn mic on and talk**. Turning the mic on clears it. |
| Take the alert down | `POST /api/calls/attention {"kind": "clear"}` | The card and the alert stop. They also stop when the call ends. |
| Get a line from you | listen for data messages on topic `owner` | Type in **Tell the agent what to say…** and press **Say** (or `agent-bluetooth say "..."`). |
| Hand the call to you | notice a non-agent participant join with its mic on | **Mic on**: you are on the line. Mic off again: the agent can carry on. |
| Report how it went | `POST /api/calls/summary {"room", "outcome", "transcript"}` | An activity line with the outcome; Telegram/webhook sinks get it too. |

## Attention: the alert card

```bash
curl -X POST http://127.0.0.1:8080/api/calls/attention -H 'content-type: application/json' \
  -d '{"kind": "question", "text": "What ZIP code is on the account?", "room": "<the room>"}'
```

- `kind`: `person`, `question` or `clear`. Anything else is a 400.
- `text`: one short line; the card shows up to four.
- `room` or `call_id`: which call. Optional; with neither, the event carries
  no call id and the app shows it for the call on screen.
- The daemon publishes a `call.attention` event (`{call_id, kind, text}`).
  Agent Phone reacts to it; anything else can too
  (`agent-bluetooth wait call.attention`, webhook sinks, automations).
- Try it without a call: `agent-bluetooth attention question "Test: are you there?"`,
  then `agent-bluetooth attention clear`.

Raise `question` only when the agent is really stuck, and send `clear` once
it has the answer. An alert that fires for nothing teaches the owner to
ignore it.

## The owner's lines: topic `owner`

`agent-bluetooth say` and the app's Say box (and its answer field) publish a
reliable data message into the call's room from the `phone` participant:

```json
{"text": "Make it 8 instead", "call_id": "3f2a9c1d7b44"}
```

on topic `owner`. One contract covers both uses:

- If the agent has a question open, the next owner line **is the answer**.
  Resolve the waiting question with it and say nothing on the line.
- Otherwise it is a line to put on the call. `agent.py` makes the model say
  it in its own words; `ivr_agent.py` says it word for word, because menus
  need exact words.

In a LiveKit agent:

```python
ctx.room.on("data_received", lambda p: p.topic == "owner" and on_owner(json.loads(p.data)["text"]))
```

`serve_owner()` in [agent.py](../examples/livekit-agent/agent.py) does this
and also reports owner joins and leaves; reuse it.

## Asking the owner, as a tool

The pattern in `ivr_agent.py`, cut down:

```python
@function_tool
async def ask_owner(self, question: str) -> str:
    """Silently ask the owner for a fact you don't have."""
    self.attention("question", question)          # card + alert in Agent Phone
    self._pending = asyncio.get_running_loop().create_future()
    try:
        async with asyncio.timeout(180):
            answer = await self._pending          # set by owner_says()
    except TimeoutError:
        return "The owner did not answer. Ask them to hold, or move on."
    finally:
        self._pending = None
        self.attention("clear")
    return f"The owner says: {answer}"

def owner_says(self, text: str) -> None:          # every topic-"owner" message
    if self._pending and not self._pending.done():
        self._pending.set_result(text)            # an answer, not a line
    else:
        ...                                       # put it on the call
```

While it waits, the agent says nothing to a machine, and to a person only
"one moment please".

## Handing the call to the owner

The daemon doesn't decide who talks; the agent does. What the examples do:

- `agent.py`: when a participant that is neither `phone` nor an agent joins,
  it says "one moment, <owner> is joining", then turns its audio in and out
  off. When that participant leaves, it takes the call back.
- `ivr_agent.py`: the owner is in the room from the start as a listener, so a
  join is not a handoff. When a person answers it raises `person`, says "one
  moment please" and goes quiet; the owner turns the mic on.

## Identity details the call may need

Account numbers, dates of birth and the like go into the worker's
environment, never into the task, dispatch metadata, events or summaries
(those reach logs, Telegram and webhooks). `ivr_agent.py` reads them from env
vars named in the call profile's `secrets`, masks them in its live log and
transcripts, and keys them in rather than saying them.

## Related

- [LIVEKIT.md](LIVEKIT.md): the room, the `phone` participant, DTMF, placing calls.
- [examples/livekit-agent/README.md](../examples/livekit-agent/README.md): running both example agents.
- [skills/phone-menu-calls](../skills/phone-menu-calls/SKILL.md): how a coding agent tunes the IVR agent for a new line.
