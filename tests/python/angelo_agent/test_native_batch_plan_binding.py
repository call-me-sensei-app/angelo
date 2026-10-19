# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Object Mode pilot's batch-edit plan, through real Codex delivery and plan validation; no Blender or provider.

In the batch-edit trials of a recorded Object Mode pilot run (GPT-6.1 Sol) the
request named only Batch_00 and Batch_11, so the turn context listed exact
revisions for those two objects alone. The model searched ``Batch_`` (all twelve rows with exact revisions), then wrote one
program of twelve transforms without ``write_set``. The host rejected it before
running anything: "o5 has no revision in your latest observation", because a
search result never became an inferred binding. The model then rewrote the
same twelve transforms with an explicit ``write_set``, a second turn.

Delivered search rows are now inferred evidence (``query_observations``,
finalized by ``codex_delivery``). These tests replay that plan exactly as the
model wrote it (strict form, decoded by ``codex_plan.parse_plan``) and validate
it with the host's own ``providers._plan_problems``: rejected with the pilot's
message when the search was never delivered, accepted with every binding taken
from the delivered rows when it was.
"""

import json
import sys
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.codex_plan import parse_plan, plan_tools
from angelo_agent.context_ledger import compact_observation
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.identifiers import IdentifierTable
from angelo_agent.providers import _plan_problems
from test_native_codex_launch import ProviderFixture

EPOCH = "f9c8ba997bd24709a44ac6c4ac3d8af0"
NAMES = ["Batch_%02d" % index for index in range(12)]
# Session UIDs and long handles as the pilot's search returned them.
HANDLES = {name: "scn:%s:%d" % (EPOCH, 164 + index) for index, name in enumerate(NAMES)}
REVISIONS = {name: "%064x" % (0xA11CE + index) for index, name in enumerate(NAMES)}
SCENE, CAMERA = "scn:%s:1" % EPOCH, "scn:%s:180" % EPOCH
LOCATIONS = {name: [2.0 * (index % 4), 2.0 * (index // 4), 0.0] for index, name in enumerate(NAMES)}
# The pilot's rejection, as the host now words it: o5 was listed without a revision (binding_refusals).
PILOT_REJECTION = ("operations[0] (angelo_scene_program): o5 has no revision in your latest observation (it is listed "
                   "without one: the observation gives revisions to the active object, objects the message names, up "
                   "to 8 selected, recently changed objects and every object of a scene of at most 8): "
                   'angelo_scene_object {"object":"o5"} returns its object_revision; declare it in read_set or '
                   "write_set")


def turn_context():
    """The pilot's first observation (long forms): revisions only for the two objects the request names."""
    mentioned = {"Batch_00", "Batch_11"}
    order = ["Batch_00", "Batch_11"] + [name for name in NAMES if name not in mentioned]
    objects = []
    for name in order:
        entry = {"name": name, "type": "MESH", "handle": HANDLES[name], "location": LOCATIONS[name],
                 "selected": False, "active": False}
        if name in mentioned:
            entry.update(mentioned=True, dimensions=[2, 2, 2], modifiers={"KeepBevel": "BEVEL"},
                         object_revision=REVISIONS[name])
        objects.append(entry)
    objects.append({"name": "KeepCamera", "type": "CAMERA", "handle": CAMERA, "location": [7, -7, 5],
                    "selected": False, "active": False})
    return {"context_version": 2, "scene": "ObjectModePilot",
            "scene_settings": {"handle": SCENE, "engine": "BLENDER_EEVEE", "revision": "5" * 64,
                               "camera": {"handle": CAMERA, "location": [7, -7, 5], "direction": [-0.465, 0.499, -0.732]}},
            "object_count": 17, "objects": objects, "active_object": None, "mode": "OBJECT", "frame_current": 1,
            "selected_count": 0, "workspace_mode": "MANUAL"}


def search_result():
    """The host's ``angelo_scene_search`` result for ``Batch_`` (long forms, every row exact)."""
    rows = [{"object": HANDLES[name], "name": name, "type": "MESH", "selected": False, "visible": True,
             "mode": "OBJECT", "collections": ["Work"], "collection_count": 1, "active": False, "asset": None,
             "revision_exact": True, "object_revision": REVISIONS[name], "program_revision_supported": True,
             "revision_limitations": []} for name in NAMES]
    return {"scene": "ObjectModePilot", "object_count": 17, "matching_count_exact": True, "snapshot_exact": True,
            "uncertain_count": 0, "truncated": False, "observation_coverage": "OBJECTS", "next_cursor": None,
            "page": {"offset": 0, "limit": 12, "returned": 12, "total": 12}, "objects": rows,
            "document_epoch": EPOCH, "read_only": True}


def pilot_plan_text(short):
    """The first plan exactly as the model wrote it: twelve SET transforms, no read_set or write_set."""
    operations = [{"kind": "transform", "target": short[name], "parameters": {
        "location": [round(LOCATIONS[name][0] + 1.25, 6), round(LOCATIONS[name][1] - .5, 6), .25],
        "rotation": [0, 0, .5], "scale": [1.5, .75, 1.25], "mode": "SET"}} for name in NAMES]
    return json.dumps({"reply": "", "operations": [{"tool": "angelo_scene_program",
                                                    "arguments": {"mode": "APPLY", "operations": operations}}]},
                      separators=(",", ":"))


class PilotBatchPlanTests(ProviderFixture):
    def setUp(self):
        super().setUp()
        self.table = self.c._identifiers = IdentifierTable("native-thread")
        self.observation = compact_observation(turn_context(), self.table)
        self.short = {entry["name"]: entry["handle"] for entry in self.observation["objects"]}
        self.result = search_result()

    def execute(self, name, arguments):
        self.assertEqual(name, "angelo_scene_search")
        return self.result

    def precondition(self, *args):
        self.fail("A search must not need approval or a mutation precondition")

    def deliver_search(self):
        """The model's one lookup, answered through the real Codex dynamic-call path."""
        self.p._request("search", "item/tool/call", {"threadId": "native-thread", "turnId": "turn-a",
                        "callId": "search", "tool": "angelo_scene_search",
                        "arguments": {"coverage": "PAGE", "query": "Batch_", "limit": 12}})
        self.c.pump_tools(self, self.c.document, budget=ToolDispatchBudget(seconds=60))
        (_identity, payload), = self.p.rpc.responses
        self.assertTrue(payload["success"])
        return json.loads(payload["contentItems"][0]["text"])

    def validate_pilot_plan(self):
        plan, problem = parse_plan(pilot_plan_text(self.short))
        self.assertIsNone(problem)
        return _plan_problems(plan["operations"], plan_tools(), self.context.resolve_call)

    def test_short_handles_match_the_pilot(self):
        self.assertEqual([self.short[name] for name in NAMES],
                         ["o3", "o5", "o6", "o7", "o8", "o9", "o10", "o11", "o12", "o13", "o14", "o4"])
        listed = {entry["name"] for entry in self.observation["objects"] if "object_revision" in entry}
        self.assertEqual(listed, {"Batch_00", "Batch_11"})

    def test_without_the_delivered_search_the_plan_is_rejected_as_in_the_pilot(self):
        problems, _operations = self.validate_pilot_plan()
        self.assertEqual(problems, [PILOT_REJECTION])

    def test_after_the_delivered_search_the_first_plan_is_accepted(self):
        shown = self.deliver_search()
        self.assertEqual([row["object"] for row in shown["objects"]], [self.short[name] for name in NAMES])
        self.assertEqual([row["object_revision"] for row in shown["objects"]],
                         [REVISIONS[name][:8] for name in NAMES])
        problems, operations = self.validate_pilot_plan()
        self.assertEqual(problems, [])
        (operation,) = operations
        arguments = operation["arguments"]
        # The bindings the corrected pilot plan declared explicitly, now inferred
        # from exactly the delivered rows: every target written, nothing read.
        self.assertEqual(sorted(arguments["write_set"], key=lambda entry: entry["handle"]),
                         sorted(({"handle": HANDLES[name], "revision": REVISIONS[name]} for name in NAMES),
                                key=lambda entry: entry["handle"]))
        self.assertFalse(arguments.get("read_set"))
        self.assertEqual([item["target"] for item in arguments["operations"]], [HANDLES[name] for name in NAMES])

    def test_a_search_row_without_an_exact_revision_still_rejects_that_object(self):
        self.result["objects"][1]["revision_exact"] = False
        self.deliver_search()
        problems, _operations = self.validate_pilot_plan()
        self.assertEqual(problems, [PILOT_REJECTION])


if __name__ == "__main__":
    unittest.main()
