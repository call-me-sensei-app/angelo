# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds of ``test_blender_reply_stream.py``. Dependency-free.

Every plan streams through the stand-in ``claude`` CLI as Claude Code 2.1.293
writes a StructuredOutput call with fine-grained tool streaming
(``fake_provider_cli.plan_events``): ``input_json_delta`` fragments a few
characters long, the reply among them, then the whole call as an assistant
message and the runtime's verdict on it, as the rc3 sessions recorded them.
"""

import os
from pathlib import Path

SESSION = "fixture-reply-stream"
# Not a credential: a made-up key, set as the connection's key for one turn, that the chat must never draw.
KEY = "sk-ant-fake-7Hq2-Wn9c-Rt4x-Lp8z"


def operation(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


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


def round_events(message, plans, chars, seconds, outcome, pause=0.0, before=0.0, gate=None):
    """One model message writing ``plans`` as StructuredOutput calls, then ``outcome`` (the runtime's verdict).

    With a ``gate`` folder, each fragment waits for the suite to acknowledge the one before
    (``fake_provider_cli.wait_for_gate``).
    """
    events = [{"sleep": before}] if before else []
    events.append(stream({"type": "message_start", "message": {"id": message, "model": "claude-opus-5-5", "usage": {
        "input_tokens": 100, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 0, "output_tokens": 1}}}))
    events += [{"stream_plan": dict({"plan": plan, "index": index, "tool_id": tool_id(message, index), "chars": chars,
                                     "seconds": seconds, "session": SESSION}, **({"gate": str(gate)} if gate else {}))}
               for index, plan in enumerate(plans)]
    events.append(stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 200}}))
    return events + ([{"sleep": pause}] if pause else []) + outcome


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


def grow_rounds():
    return [answered("msg_grow", [GROW], 10, 0.12)]


def refused_rounds():
    # The reply is whole on screen for a moment before the runtime's verdict, as when it validates a long plan.
    return [round_events("msg_refused", [REFUSED], 6, 0.1, refused("msg_refused"), pause=1.5)]


def stopped_rounds():
    return [answered("msg_stopped", [STOPPED], 8, 0.3)]


def withdrawal_rounds():
    """A lookup whose plan replies; a later round whose plan is withdrawn; a corrected answer after a pause."""
    return [answered("msg_lookup", [LOOKUP], 12, 0.05),
            answered("msg_withdrawn", list(WITHDRAWN), 8, 0.15, pause=1.0),
            answered("msg_answer", [ANSWER], 12, 0.05, before=3.0)]


def keyed_streams(decoder):
    """The reply the chat streams after each fragment of ``KEYED``, as ``decoder`` (a ``PlanReply``) reads them."""
    import fake_provider_cli
    text, streams = "", []
    for fragment in fake_provider_cli.plan_fragments(KEYED, KEYED_CHARS):
        text += decoder.feed(fragment)
        streams.append(text)
    return streams


def held_back(raw):
    """How many characters at the end of ``raw`` could begin the key (the chat holds them back)."""
    return next((length for length in range(len(KEY) - 1, 2, -1) if raw.endswith(KEY[:length])), 0)


def gate_sent(gate):
    """The fragment the stand-in sent last and holds the next one after (``wait_for_gate``); -1 before the first."""
    try:
        return int((Path(gate) / "sent").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return -1


def acknowledge(gate, fragment):
    """Tell the stand-in that fragment ``fragment``'s frame was drawn and checked: it sends the next."""
    temporary = Path(gate) / "ack.tmp"
    temporary.write_text(str(fragment), encoding="utf-8")
    os.replace(temporary, Path(gate) / "ack")


def key_rounds(gate):
    """The lookup carrying the key, then the reply repeating it, fragment by fragment behind ``gate``.

    The suite acknowledges each fragment once it has drawn and checked its frame, so every state of the
    split key is drawn however busy the machine is; nothing depends on a timer catching it.
    """
    return [answered("msg_key_lookup", [KEY_LOOKUP], 12, 0.05),
            answered("msg_key", [KEYED], KEYED_CHARS, 0.05, gate=gate)]
