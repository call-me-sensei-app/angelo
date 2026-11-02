# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds of ``test_blender_plan_progress.py``. Dependency-free.

Each plan streams through the stand-in ``claude`` CLI as Claude Code 2.1.293 writes a StructuredOutput call with
fine-grained tool streaming (``fake_provider_cli.plan_events``). The timed round streams at the pace Opus 5.5 wrote
the arched footbridge in a recorded run (about 260 characters a second, after a thinking block), so the suite can
time when the live row first shows a provisional step against when the plan began and when its reply arrived.

Every moment the suite observes is held by the stand-in until the suite has seen it (1.0.8), never timed with a
sleep it has to catch: a plan behind a ``gate`` folder sends each fragment only once the suite has acknowledged the
one before, and a pause (a thinking block, the gap after a refusal) is a ``fake_provider_cli.hold`` the suite
releases. A stall of the app's main thread longer than a sleep applied both sides of it in one pump pass.
"""

import json

import fake_provider_cli
import reply_stream_fixtures as base

SESSION = base.SESSION
CHARS, SECONDS = 16, 0.06  # 16 characters every 60 ms: 267 a second (the timed round's pace).


def cube(name, alias, location, scale):
    return {"kind": "create_primitive", "parameters": {"primitive": "CUBE", "name": name, "alias": alias, "size": 1,
                                                       "location": location, "scale": scale}}


def footbridge(prefix=""):
    """The rc6 trial's program (its deck a cube, so no script needs review): deck, four posts, two rails, stone."""
    posts = [cube(prefix + "Post_" + corner, "p%d" % index, [x, y, 0.7], [0.15, 0.15, 1])
             for index, (corner, x, y) in enumerate((("FL", -1.9, -0.55), ("FR", 1.9, -0.55), ("BL", -1.9, 0.55),
                                                     ("BR", 1.9, 0.55)), 1)]
    rails = [cube(prefix + "Rail_" + side, "r%d" % index, [0, y, 1.15], [3.95, 0.1, 0.1])
             for index, (side, y) in enumerate((("Front", -0.55), ("Back", 0.55)), 1)]
    stone = {"kind": "material_principled", "target": "$d", "parameters": {
        "name": prefix + "Grey Stone", "inputs": {"Base Color": [0.42, 0.42, 0.4, 1], "Roughness": 0.9},
        "also": ["$p1", "$p2", "$p3", "$p4", "$r1", "$r2"]}}
    return {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
        cube(prefix + "Deck", "d", [0, 0, 0.3], [4, 1.2, 0.2])] + posts + rails + [stone])]}


def batched_footbridge(prefix=""):
    """The same bridge as Claude's plan writes it once batch is exact: one post and one rail, then a batch each."""
    stone = {"kind": "material_principled", "target": "$d", "parameters": {
        "name": prefix + "Grey Stone", "inputs": {"Base Color": [0.42, 0.42, 0.4, 1], "Roughness": 0.9},
        "also": ["$p", "$r"]}}
    posts = {"kind": "batch", "parameters": {"sources": ["$p"], "at": [
        [-1.9, -0.55, 0.7], [1.9, -0.55, 0.7], [-1.9, 0.55, 0.7], [1.9, 0.55, 0.7]]}}
    rails = {"kind": "batch", "parameters": {"sources": ["$r"], "mirror": "Y"}}
    return {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
        cube(prefix + "Deck", "d", [0, 0, 0.3], [4, 1.2, 0.2]),
        cube(prefix + "Post", "p", [-1.9, -0.55, 0.7], [0.15, 0.15, 1]),
        cube(prefix + "Rail", "r", [0, -0.55, 1.15], [3.95, 0.1, 0.1]), stone, posts, rails])]}


BUILD = footbridge()
TIMED = footbridge("Timed ")  # The timed round's plan: the same bridge, its objects apart from the held round's.
OLD, SLOW, ARABIC = footbridge("Old "), footbridge("Slow "), batched_footbridge("Ar ")
BATCHED_ROWS = ["Deck", "Post ×4", "Rail ×2", "Grey Stone"]
# What the live row shows once the whole plan is written (chat_rows.planned_label of plan_steps.groups).
BUILD_ROWS = ["Deck", "Post ×4", "Rail ×2", "Grey Stone"]
FIRST_ROW = "Deck"
ANSWER = {"operations": [], "reply": "Nothing was built: tell me where the footbridge should go."}


def plan_statuses(plan, chars, scan, status):
    """The status the chat holds after each ``chars``-character fragment of ``plan``.

    ``scan`` is the app's ``PlanProgress``, reading the text as it arrives, and ``status(scan)`` the status the
    app computes from it; passed in by the suite, so this module needs nothing of the app.
    """
    statuses = []
    for fragment in fake_provider_cli.plan_fragments(plan, chars):
        scan.feed(fragment)
        statuses.append(status(scan))
    return statuses


def first_row_seconds(plan, chars=CHARS, seconds=SECONDS):
    """When the stand-in sends the fragment that completes the plan's first name, from the plan block's start."""
    text = json.dumps(plan, ensure_ascii=False)
    first = plan["operations"][0]["arguments"]["operations"][0]["parameters"]["name"]
    end = text.index(json.dumps(first)) + len(json.dumps(first))
    return (end - 1) // chars * seconds


def thinking(hold):
    """A thinking block before the plan, as Opus 5.5 thinks first (5-9 s live; its text omitted).

    It lasts until the suite releases ``hold`` (``fake_provider_cli.hold``), so the plan never starts before the
    suite is ready for it, however busy the app is.
    """
    block = {"type": "thinking", "thinking": ""}
    return [base.stream({"type": "content_block_start", "index": 0, "content_block": block}),
            fake_provider_cli.hold(hold), base.stream({"type": "content_block_stop", "index": 0})]


def build_rounds(plan, message, hold, gate=None):
    """One round: thinking, then the plan, accepted; the host then runs it.

    Without a ``gate`` folder the plan streams at Opus's pace (the timed round); with one, fragment by fragment as
    the suite acknowledges them (``fake_provider_cli.wait_for_gate``).
    """
    events = base.answered(message, [plan], CHARS, 0 if gate else SECONDS, gate=gate)
    events[1:1] = thinking(hold)  # After message_start, before the plan call.
    for item in events:
        if "stream_plan" in item:
            item["stream_plan"]["index"] = 1
    return [events]


def refused_then_answered_rounds(gate, gap):
    """The runtime's schema check refuses the plan after it streamed; the model answers after a pause.

    The plan streams behind ``gate``; the pause between the refusal and the model's next call is the hold ``gap``.
    """
    refused = [base.tool_result(base.tool_id("msg_refused_build", 0),
                                "Output does not match required schema: " + base.SCHEMA_ERROR, error=True),
               fake_provider_cli.hold(gap)]
    retry = base.answered("msg_retry", [ANSWER], CHARS, SECONDS)
    return [base.round_events("msg_refused_build", [OLD], CHARS, 0, refused + retry, gate=gate)]


def stopped_rounds(gate):
    """A plan held fragment by fragment behind ``gate``, so it is still being written when the suite stops it."""
    return [base.answered("msg_slow", [SLOW], CHARS, 0, gate=gate)]


# A plan whose first object is named with the connection's key, streamed in five-character fragments.
KEYED = {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
    cube("Sign " + base.KEY, "s", [0, 0, 0], [1, 1, 1]), cube("Sign Post", "t", [0, 0, -1], [0.1, 0.1, 1])])]}
KEYED_CHARS = 5


def keyed_rounds(gate):
    """``KEYED`` behind ``gate``, then refused."""
    return [base.round_events("msg_keyed", [KEYED], KEYED_CHARS, 0, base.refused("msg_keyed"), gate=gate)]
