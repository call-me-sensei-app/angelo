# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A read, a look, an edit and a look again, in one plan or turn, on each runtime. No Blender.

The live read tools (``angelo_read_data``, ``angelo_blend_file``) and the view
tool are all read-only lookups. This suite runs the three together with an
edit over the real providers and scripted runtimes (``read_tools_stand_ins``),
with a stand-in scene whose colour the edit changes from red to blue. Every
picture is a real PNG, decoded again from what the runtime would hand its
model:

* the reads' answers and both looks reach the model, in order;
* the look before the edit shows the scene before it and the look after it the
  scene after it; in a Claude plan the earlier one is labelled as taken before
  the edit and a kept viewport view is captured again after it;
* a read that fails stops a Claude plan at its edit (the lookups before the
  edit still run) and is a failed answer inside a Codex turn; either way the
  model reads why and the turn goes on (``plan_recovery``).
"""

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import picture_fakes  # noqa: E402
from angelo_agent.claude_evidence import STALE_LOOK  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.tool_specs import CODEX_DEFERRED_NAMESPACE, TOOL_MAP  # noqa: E402
from read_tools_stand_ins import ClaudeStandIn, CodexStandIn, final, plan  # noqa: E402

READ, FILE, VIEW, EDIT = "angelo_read_data", "angelo_blend_file", "angelo_view", "angelo_set_transform"
RED, BLUE = (220, 30, 30), (30, 30, 220)
LOOK = {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}
LIT = {"views": ["FRONT"], "shading": "LIT", "resolution": 256}
MOVE = {"object": "Cube", "location": [0, 1, 0]}
PATH = {"path": 'data.worlds["World"].color'}


class Scene:
    """The host side: one colour that a read reports, a view shows and an edit changes."""

    def __init__(self, references):
        self.references, self.colour, self.read_error = references, RED, None

    def picture(self, arguments):
        angle, shading = arguments["views"][0], arguments["shading"]
        record = self.references.new_observation("%s view · %s" % (angle, "rendered" if shading == "RENDERED" else shading.lower()))
        pixels = numpy.empty((36, 64, 3), dtype=numpy.uint8)
        pixels[...] = self.colour
        Path(record["path"]).write_bytes(picture_fakes.png_bytes(pixels))
        return dict(record, view=angle, shading=shading, width=64, height=36)

    def execute(self, name, arguments):
        if name == READ:
            if self.read_error:
                return {"error": self.read_error}
            return {"found": True, "read": arguments["path"], "kind": "VALUE", "value": [c / 255 for c in self.colour],
                    "read_only": True, "scene_changed": False}
        if name == FILE:
            return {"readable": True, "scope": "OPEN_FILE", "saved": True, "unsaved_changes": self.colour == BLUE,
                    "datablocks": [{"type": "worlds", "count": 1, "unused": 0}], "read_only": True, "scene_changed": False}
        if name == VIEW:
            result = {"success": True, "read_only": True, "scene_changed": False, "views": [self.picture(arguments)],
                      "shading": arguments["shading"]}
            if arguments["shading"] == "RENDERED":
                result["rendered"] = {"engine": "CYCLES", "complete": True, "seconds": 4.0}
            return result
        if name == EDIT:
            self.colour = BLUE
            return {"success": True, "object": "Cube", "location": arguments["location"]}
        return {"scene": "Scene", "object_count": 1, "objects": [], "truncated": False}


def colour_of(png):
    pixels = picture_fakes.read_png(png)
    return tuple(int(value) for value in pixels[10, 10, :3])


class ToolTests(unittest.TestCase):
    def test_the_three_lookups_are_read_only_tools(self):
        for name in (READ, FILE, VIEW):
            annotations = TOOL_MAP[name]["annotations"]
            self.assertTrue(annotations["readOnlyHint"] and not annotations["destructiveHint"], name)


class ClaudePlanTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        references = ReferenceStore(self.folder.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        self.scene = Scene(references)
        self.claude = ClaudeStandIn(self.scene.execute)
        self.addCleanup(self.claude.close)
        self.claude.host.references = references
        self.attached = []  # What each round was sent as images: (name, colour).
        encode = references.claude_inputs

        def attach(records):
            blocks = encode(records)
            self.attached.append([(record["name"], colour_of(base64.b64decode(block["source"]["data"])))
                                  for record, block in zip(records, blocks)])
            return blocks
        references.claude_inputs = attach

    def test_a_read_a_look_an_edit_and_a_look_in_one_plan(self):
        status = self.claude.run(
            "What colour is the world? Make it blue and show me.",
            plan((READ, PATH), (FILE, {}), (VIEW, LIT), (VIEW, LOOK), (EDIT, MOVE), (VIEW, LOOK), verify=True),
            plan(reply="It was red and is blue now."))
        self.assertEqual(status, "completed")
        names = [(name, arguments.get("shading")) for name, arguments, _result in self.claude.dispatcher.calls]
        self.assertEqual(names[:6], [(READ, None), (FILE, None), (VIEW, "LIT"), (VIEW, "RENDERED"), (EDIT, None),
                                     (VIEW, "RENDERED")], "in the order of the plan")
        self.assertEqual(names[6], (VIEW, "LIT"), "the kept viewport view is captured again after the edit")
        self.assertEqual(sum(1 for name, shading in names if shading == "RENDERED"), 2, "a look is never rendered again")
        (images,) = self.attached
        self.assertEqual(sorted(images), sorted([
            ("FRONT view · lit", BLUE),
            ("SCENE_CAMERA view · rendered" + STALE_LOOK, RED),
            ("SCENE_CAMERA view · rendered", BLUE)]), "the earlier look is history, the later one and the kept view are current")
        told = json.loads(self.claude.prompts[1].split("\n", 1)[1])
        self.assertEqual(told["result"]["operations"][0]["value"], [value / 255 for value in RED], "the read's answer")
        self.assertEqual(told["result"]["operations"][1]["datablocks"], [{"type": "worlds", "count": 1, "unused": 0}])
        self.assertEqual(len(told["visual_evidence"]), 3)
        self.assertNotIn("stale_views", told["result"])
        self.assertFalse([row for row in self.claude.system_rows() if "rejected" in row or "stopped" in row])

    def test_a_read_that_fails_stops_the_edit_and_the_model_still_gets_its_looks(self):
        self.scene.read_error = "The document changed while its data was being read; read it again"
        status = self.claude.run(
            "What colour is the world? Make it blue and show me.",
            plan((READ, PATH), (VIEW, LOOK), (EDIT, MOVE), (VIEW, LOOK), verify=True),
            plan(reply="I could not read the world, so I changed nothing; it looks red."))
        self.assertEqual(status, "completed")
        names = [name for name, _arguments, _result in self.claude.dispatcher.calls]
        self.assertEqual(names[:2], [READ, VIEW])
        self.assertNotIn(EDIT, names, "written without the answer it asked for: not run")
        self.assertEqual(self.scene.colour, RED)
        note = self.claude.prompts[1].split("\n", 1)[0]
        self.assertIn("operations[0] (angelo_read_data) failed: The document changed while its data was being read", note)
        self.assertIn("operations[2..3] did not run.", note)
        (images,) = self.attached
        self.assertEqual(images, [("SCENE_CAMERA view · rendered", RED)], "the look that ran is current: nothing was edited")


class CodexTurnTests(unittest.TestCase):
    def setUp(self):
        self.codex = CodexStandIn(lambda name, arguments: self.scene.execute(name, arguments))
        self.addCleanup(self.codex.close)
        self.scene = Scene(self.codex.controller.references)

    def pictures(self):
        prefix = "data:image/png;base64,"
        return [colour_of(base64.b64decode(item["imageUrl"][len(prefix):]))
                for _identity, payload in self.codex.server.answers for item in payload["contentItems"]
                if item["type"] == "inputImage"]

    def test_a_read_a_look_an_edit_and_a_look_in_one_turn(self):
        status = self.codex.run("What colour is the world? Make it blue and show me.", [
            ("tool", READ, PATH), ("tool", FILE, {}, CODEX_DEFERRED_NAMESPACE), ("tool", VIEW, LOOK), ("tool", EDIT, MOVE),
            ("tool", VIEW, LOOK), ("tool", FILE, {}, CODEX_DEFERRED_NAMESPACE), final("It was red and is blue now.")])
        self.assertEqual(status, "completed")
        answers = self.codex.answers()
        self.assertEqual([success for success, _answer in answers], [True] * 6)
        self.assertEqual(answers[0][1]["value"], [value / 255 for value in RED])
        self.assertEqual((answers[1][1]["unsaved_changes"], answers[5][1]["unsaved_changes"]), (False, True),
                         "the file tool reads the document as it is at each call")
        self.assertEqual([answer.get("shading") for _success, answer in answers],
                         [None, None, "RENDERED", None, "RENDERED", None])
        self.assertEqual(self.pictures(), [RED, BLUE], "each look is the scene as it was when it was taken")

    def test_a_read_that_fails_is_a_failed_answer_and_the_turn_goes_on(self):
        self.scene.read_error = "The document changed while its data was being read; read it again"
        status = self.codex.run("What colour is the world?", [
            ("tool", READ, PATH), ("tool", VIEW, LOOK), final("I could not read it; it looks red.")])
        self.assertEqual(status, "completed")
        self.assertEqual([success for success, _answer in self.codex.answers()], [False, True])
        self.assertEqual(self.pictures(), [RED])


if __name__ == "__main__":
    unittest.main()
