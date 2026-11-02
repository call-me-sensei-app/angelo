# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds of ``test_blender_reply_stream.py``. Dependency-free.

Every plan streams through the stand-in ``claude`` CLI as Claude Code 2.1.293
writes a StructuredOutput call with fine-grained tool streaming
(``fake_provider_cli.plan_events``): ``input_json_delta`` fragments a few
characters long, the reply among them, then the whole call as an assistant
message and the runtime's verdict on it, as the rc3 sessions recorded them.

A moment the suite observes is held by the stand-in until the suite has seen
it (1.0.8): a reply streamed fragment by fragment behind a gate, a call held
while it is still written (``round_events``' ``written``), or a
``fake_provider_cli.hold`` before a round or its verdict. Pauses timed with
sleeps raced the suite's main-thread tick: a stall longer than the pause
applied both sides of it in one pump pass, and the moment was never drawn.
"""

import fake_provider_cli
from fake_provider_cli import acknowledge, gate_sent  # noqa: F401  (the suites' gate, one owner)

SESSION = "fixture-reply-stream"
# Not a credential: a made-up key, set as the connection's key for one turn, that the chat must never draw.
KEY = "sk-ant-fake-7Hq2-Wn9c-Rt4x-Lp8z"


def operation(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


GROW_CHARS, STOPPED_CHARS = 10, 8  # Their fragments' sizes; the suite reads each fragment's state (reply_streams).
GROW = {"operations": [], "reply": (
    "The default scene holds three objects: a cube at the origin, a camera that looks at it from the front right, "
    "and a point light above and behind it. The cube is a two metre mesh with one grey material, the camera uses a "
    "fifty millimetre lens, and the light is a thousand watt point lamp. Nothing is hidden or disabled, and there "
    "are no modifiers, constraints or keyframes yet, so any of them can be changed or replaced freely.")}
# A program the runtime's schema check refuses (no operations), whose reply claims work that never ran.
REFUSED = {"operations": [operation("angelo_scene_program", mode="APPLY", read_set=[], write_set=[], operations=[])],
           "reply": "Deleted the three cubes."}
SCHEMA_ERROR = "/operations/0/arguments/operations: must NOT have fewer than 1 items"
STOPPED = {"operations": [], "reply": (
    "Here is a careful walk through every light in the scene, one at a time, with its power, colour, shadow "
    "softness and the objects it reaches, so you can decide which of them to keep.")}
LOOKUP = {"operations": [operation("angelo_agent_capabilities", family="CHECKS", names=["mesh_manifold"])],
          "reply": "First I will look up what the manifold check is called.", "verify": True}
# Two calls in one message: the runtime returns the last, which drops the first one's edit (superseded_plans).
WITHDRAWN = ({"operations": [operation("angelo_create_cube", name="Teal Cube", size=1, location=[3, 0, 0])],
              "reply": "Also adding a teal cube beside it."},
             {"operations": [], "reply": "All set, the teal cube is in."})
ANSWER = {"operations": [], "reply": "The check is named mesh_manifold; a closed mesh passes it."}
# A lookup whose operation carries the key (as a script's code can): its plan call's row and its own row hold it.
KEY_LOOKUP = {"operations": [operation("angelo_scene_search", query=KEY)],
              "reply": "First I will check that no object is named after it.", "verify": True}
KEYED = {"operations": [], "reply": "The key you set up, " + KEY + ", is used only for this connection."}
KEYED_CHARS = 5  # Its fragments' size: the key arrives split across several, each one drawn (key_rounds' gate).


def work_group(messages, call_id):
    """The chat's key for the work group a step's row folds into: the first row of its run of steps.

    ``chat_rows.group_blocks`` folds each run of two or more consecutive activity or thinking rows behind one
    closed group row keyed ``("group", <the run's first row>)``, so a suite opens that row before the step's own.
    """
    end = next(index for index, message in enumerate(messages) if message.get("call_id") == call_id)
    start = end
    while start and messages[start - 1]["role"] in {"activity", "thinking"}:
        start -= 1
    return ("group", messages[start].get("call_id"))


def stream(event):
    return {"type": "stream_event", "event": event, "session_id": SESSION}


def tool_id(message, index):
    return "toolu_%s_%d" % (message, index)


def round_events(message, plans, chars, seconds, outcome, gate=None, written=None):
    """One model message writing ``plans`` as StructuredOutput calls, then ``outcome`` (the runtime's verdict).

    With a ``gate`` folder, each fragment of the one call waits for the suite to acknowledge the one before
    (``fake_provider_cli.wait_for_gate``); fragment numbers restart with each call, so a gate serves one call.
    ``written`` maps a call's index to a hold folder: that call stops after its last fragment, still being written
    (before its block ends), until the suite releases it. A hold before the verdict is ``outcome``'s first item.
    """
    assert gate is None or len(plans) == 1, "A gate numbers one call's fragments: gate a round of one call"
    events = [stream({"type": "message_start", "message": {"id": message, "model": "claude-opus-5-5", "usage": {
        "input_tokens": 100, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 0, "output_tokens": 1}}})]
    for index, plan in enumerate(plans):
        call = dict({"plan": plan, "index": index, "tool_id": tool_id(message, index), "chars": chars,
                     "seconds": seconds, "session": SESSION}, **({"gate": str(gate)} if gate else {}))
        if index not in (written or {}):
            events.append({"stream_plan": call})
            continue
        call = fake_provider_cli.plan_events(**call)
        end = next(number for number, event in enumerate(call)
                   if event.get("event", {}).get("type") == "content_block_stop")
        events += call[:end] + [fake_provider_cli.hold(written[index])] + call[end:]
    events.append(stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 200}}))
    return events + outcome


def tool_result(identity, text, error=False):
    return {"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": identity, "is_error": error, "content": text}]}}


def accepted(message, plans):
    """The runtime accepts every call and returns the last one's input."""
    return [tool_result(tool_id(message, index), "Structured output provided successfully")
            for index in range(len(plans))] + [
        {"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "duration_ms": 1000,
         "structured_output": plans[-1]}]


def refused(message):
    """The runtime's schema check refuses the call, and it gives up on the round."""
    return [tool_result(tool_id(message, 0), "Output does not match required schema: " + SCHEMA_ERROR, error=True),
            {"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
             "session_id": SESSION, "duration_ms": 1000, "errors": ["Output does not match required schema"]}]


def answered(message, plans, chars, seconds, **timing):
    return round_events(message, plans, chars, seconds, accepted(message, plans), **timing)


def grow_rounds(gate):
    """The answer, fragment by fragment behind ``gate``: the suite draws the frames it compares as the reply grows."""
    return [answered("msg_grow", [GROW], GROW_CHARS, 0, gate=gate)]


def refused_rounds(verdict):
    """The reply is whole on screen, held at ``verdict`` until drawn, before the runtime refuses its plan."""
    return [round_events("msg_refused", [REFUSED], 6, 0.1, [fake_provider_cli.hold(verdict)] + refused("msg_refused"))]


def stopped_rounds(gate):
    """A long answer behind ``gate``: the suite stops it while the stand-in holds the rest of the plan."""
    return [answered("msg_stopped", [STOPPED], STOPPED_CHARS, 0, gate=gate)]


def withdrawal_rounds(written, verdict, opening):
    """A lookup whose plan replies; a later round whose plan is withdrawn; a corrected answer.

    Three holds: the withdrawn call while still written (``written``), the later call's reply before the runtime's
    verdict (``verdict``), and the turn after the withdrawal, before the corrected answer starts (``opening``).
    """
    message = "msg_withdrawn"
    return [answered("msg_lookup", [LOOKUP], 12, 0.05),
            round_events(message, list(WITHDRAWN), 8, 0.15,
                         [fake_provider_cli.hold(verdict)] + accepted(message, list(WITHDRAWN)), written={0: written}),
            [fake_provider_cli.hold(opening)] + answered("msg_answer", [ANSWER], 12, 0.05)]


def reply_streams(plan, chars, decoder):
    """The reply the chat streams after each fragment of ``plan``, as ``decoder`` (a ``PlanReply``) reads them."""
    text, streams = "", []
    for fragment in fake_provider_cli.plan_fragments(plan, chars):
        text += decoder.feed(fragment)
        streams.append(text)
    return streams


def keyed_streams(decoder):
    """The reply the chat streams after each fragment of ``KEYED`` (``reply_streams``)."""
    return reply_streams(KEYED, KEYED_CHARS, decoder)


class GatedReply:
    """A plan's reply the stand-in streams behind a gate folder, one fragment per acknowledgement, as a suite follows it.

    ``streams`` holds what the chat streams after each fragment (``reply_streams``), so the suite knows when the app
    has applied the fragment the stand-in holds at; it observes that state for as long as it needs, then
    acknowledges it and the next fragment is sent.
    """

    def __init__(self, folder, plan, chars, decoder):
        self.folder, self.streams, self.acknowledged = folder, reply_streams(plan, chars, decoder), -1

    def open(self):
        """Whether fragments remain to be acknowledged."""
        return self.acknowledged < len(self.streams) - 1

    def applied(self, streaming):
        """The fragment the stand-in holds at once the chat's reply (``streaming``) shows it, else None."""
        sent = gate_sent(self.folder)
        shown = sent > self.acknowledged and (not self.streams[sent] or streaming == self.streams[sent])
        return sent if shown else None

    def acknowledge(self, fragment):
        """Fragment ``fragment`` was observed: the stand-in sends the next (every fragment, past the last)."""
        acknowledge(self.folder, fragment)
        self.acknowledged = fragment


def held_back(raw):
    """How many characters at the end of ``raw`` could begin the key (the chat holds them back)."""
    return next((length for length in range(len(KEY) - 1, 2, -1) if raw.endswith(KEY[:length])), 0)


def key_rounds(gate):
    """The lookup carrying the key, then the reply repeating it, fragment by fragment behind ``gate``.

    The suite acknowledges each fragment once it has drawn and checked its frame, so every state of the
    split key is drawn however busy the machine is; nothing depends on a timer catching it.
    """
    return [answered("msg_key_lookup", [KEY_LOOKUP], 12, 0.05),
            answered("msg_key", [KEYED], KEYED_CHARS, 0.05, gate=gate)]
