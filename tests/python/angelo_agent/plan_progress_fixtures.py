# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds of ``test_blender_plan_progress.py``. Dependency-free.

Each plan streams through the stand-in ``claude`` CLI as Claude Code 2.1.293 writes a StructuredOutput call with
fine-grained tool streaming (``fake_provider_cli.plan_events``), at the pace Opus 5.5 wrote the arched footbridge
in a recorded run (about 260 characters a second, after a thinking block), so the suite can time when the
live row first shows a provisional step against when the plan began and when its reply arrived.
"""

import json

import reply_stream_fixtures as base

SESSION = base.SESSION
CHARS, SECONDS = 16, 0.06  # 16 characters every 60 ms: 267 a second.
THINKING = 1.2  # The thinking block before the plan (5-9 s live; shorter here to keep the suite quick).


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
BATCHED_ROWS = ["Deck", "Post ×4", "Rail ×2", "Grey Stone"]
# What the live row shows once the whole plan is written (chat_rows.planned_label of plan_steps.groups).
BUILD_ROWS = ["Deck", "Post ×4", "Rail ×2", "Grey Stone"]
FIRST_ROW = "Deck"
ANSWER = {"operations": [], "reply": "Nothing was built: tell me where the footbridge should go."}


def first_row_seconds(plan, chars=CHARS, seconds=SECONDS):
    """When the stand-in sends the fragment that completes the plan's first name, from the plan block's start."""
    text = json.dumps(plan, ensure_ascii=False)
    first = plan["operations"][0]["arguments"]["operations"][0]["parameters"]["name"]
    end = text.index(json.dumps(first)) + len(json.dumps(first))
    return (end - 1) // chars * seconds


def thinking():
    """A thinking block of ``THINKING`` seconds before the plan, as Opus 5.5 thinks first (its text omitted)."""
    return [base.stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            {"sleep": THINKING}, base.stream({"type": "content_block_stop", "index": 0})]


def build_rounds(plan=BUILD, message="msg_build"):
    """One round: thinking, then the plan streaming at Opus's pace, accepted; the host then runs it."""
    events = base.answered(message, [plan], CHARS, SECONDS)
    events[1:1] = thinking()  # After message_start, before the plan call.
    for item in events:
        if "stream_plan" in item:
            item["stream_plan"]["index"] = 1
    return [events]


def refused_then_answered_rounds():
    """The runtime's schema check refuses the plan after it streamed, then the model answers after a pause."""
    refused = [base.tool_result(base.tool_id("msg_refused_build", 0),
                                "Output does not match required schema: " + base.SCHEMA_ERROR, error=True),
               {"sleep": 2.0}]
    retry = base.answered("msg_retry", [ANSWER], CHARS, SECONDS)
    return [base.round_events("msg_refused_build", [footbridge("Old ")], CHARS, SECONDS, refused + retry, pause=0.8)]


def slow_rounds(prefix="Slow "):
    """A plan streamed slowly enough to be stopped while its rows show."""
    return [base.answered("msg_slow", [footbridge(prefix)], 8, 0.12)]


def keyed_rounds():
    """A plan whose first object is named with the connection's key, in five-character fragments; then refused."""
    plan = {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
        cube("Sign " + base.KEY, "s", [0, 0, 0], [1, 1, 1]), cube("Sign Post", "t", [0, 0, -1], [0.1, 0.1, 1])])]}
    return [base.round_events("msg_keyed", [plan], 5, 0.05, base.refused("msg_keyed"), pause=0.6)]
