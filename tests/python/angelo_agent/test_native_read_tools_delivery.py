# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the live read tools sit for each runtime, and that their answers reach the model. No Blender.

The real providers and controller run over scripted stand-ins
(read_tools_stand_ins.py); the tool results here are canned. The compiled-app
suite (test_blender_read_tools.py) repeats the delivery with Blender's own answers.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import codex_plan, plan_schema, providers  # noqa: E402
from angelo_agent.claude_outcomes import native_result_problem  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.external_requests import PHASE1_READ_TOOLS  # noqa: E402
from angelo_agent.job_host import SCENE_FREE  # noqa: E402
from angelo_agent.tool_specs import (COLLABORATOR_TOOLS, CODEX_DEFERRED_NAMESPACE, TOOL_MAP, codex_tools,  # noqa: E402
                                     dynamic_tools, native_plan_schema)
from read_tools_stand_ins import ClaudeStandIn, CodexStandIn, final, plan  # noqa: E402
from test_native_schema_compaction import expand_generated  # noqa: E402

READ, FILE = "angelo_read_data", "angelo_blend_file"
SOCKETS = {"found": True, "read": 'data.node_groups["Rock"].nodes[0].inputs', "kind": "COLLECTION", "length": 2,
           "items": [{"index": 0, "struct": "NodeSocketFloat", "name": "Scale", "identifier": "Planted_Scale_7",
                      "type": "VALUE", "enabled": True}],
           "next_offset": None, "truncated": False, "read_only": True, "scene_changed": False}
UNKNOWN = {"found": False, "code": "UNKNOWN_MEMBER", "resolved": "scene.cycles", "member": "feature_set",
           "message": "scene.cycles has no property feature_set.", "similar": [], "members": ["samples", "device"],
           "members_total": 2, "read_only": True, "scene_changed": False}
SUMMARY = {"readable": True, "scope": "OPEN_FILE", "saved": True, "unsaved_changes": False,
           "datablocks": [{"type": "meshes", "count": 2, "unused": 1}],
           "unused": {"total": 1, "items": [{"type": "meshes", "name": "Planted Orphan"}]},
           "external_files": {"total": 1, "missing": 1, "items": [
               {"type": "images", "name": "rock.png", "file": "textures/rock.png", "status": "missing"}]},
           "read_only": True, "scene_changed": False}
OUTSIDE = {"readable": False, "scope": "WORKSPACE_FILE", "code": "OUTSIDE_WORKSPACE",
           "message": "Use a non-hidden relative path inside this chat's file workspace", "read_only": True,
           "scene_changed": False}
ANSWERS = {('scene.cycles.feature_set',): UNKNOWN, ('data.node_groups["Rock"].nodes[0].inputs',): SOCKETS}


def canned(name, arguments):
    if name == READ:
        return ANSWERS.get((arguments["path"],), {"found": False, "code": "CALL", "position": 17, "message": "Calls are not read."})
    if name == FILE:
        return OUTSIDE if arguments.get("file") else SUMMARY
    return {"scene": "Scene", "object_count": 0, "objects": [], "truncated": False}


def variants(schema):
    """``({tool: member}, deferred tool names)`` of the plan (its tagged unions are if/then: tagged_unions)."""
    from angelo_agent.tagged_unions import members
    return members(expand_generated(schema)["properties"]["operations"]["items"])[1:]


class PlacementTests(unittest.TestCase):
    """The reader is in context where a lookup round would cost what it saves; the file tool is deferred."""

    def test_claude_plan_states_the_reader_exactly_and_names_the_file_tool(self):
        exact, deferred = variants(native_plan_schema())
        arguments = exact[READ]["properties"]["arguments"]
        self.assertEqual(arguments["required"], ["path"])
        self.assertEqual(set(arguments["properties"]), {"path", "offset", "limit", "depth"})
        self.assertIn("[index]", arguments["properties"]["path"]["description"])
        self.assertNotIn(FILE, exact)
        self.assertIn(FILE, deferred)
        self.assertIn(FILE, plan_schema.DEFERRED_TOOLS)
        self.assertNotIn(READ, plan_schema.DEFERRED_TOOLS)

    def test_a_lookup_returns_the_deferred_tools_exact_schema(self):
        found = plan_schema.lookup("TOOLS", [FILE])["tools"][0]
        self.assertEqual((found["name"], found["read_only"]), (FILE, True))
        self.assertEqual(set(expand_generated(found["input_schema"])["properties"]), {"file", "list", "offset", "limit"})
        self.assertIn(FILE, [entry["name"] for entry in plan_schema.deferred_index()["deferred_tools"]])

    def test_codex_declares_the_reader_and_defers_the_file_tool(self):
        tools = codex_tools()
        declared = {tool["name"]: tool for tool in tools if tool.get("type") == "function"}
        self.assertIn("64 rows", declared[READ]["description"])
        self.assertNotIn(FILE, declared)
        namespace = next(tool for tool in tools if tool.get("type") == "namespace")
        deferred = {tool["name"]: tool for tool in namespace["tools"]}
        self.assertEqual((namespace["name"], deferred[FILE]["deferLoading"]), (CODEX_DEFERRED_NAMESPACE, True))
        # Function-calling connections and voice threads cannot defer: both tools in full.
        for tools in (codex_tools(defer=False), dynamic_tools()):
            self.assertLessEqual({READ, FILE}, {tool["name"] for tool in tools})

    def test_neither_tool_enters_a_codex_plan_or_the_external_cli(self):
        self.assertFalse({READ, FILE} & codex_plan.PLAN_TOOLS, "A plan ends a turn with edits; reads happen in-turn")
        self.assertFalse({READ, FILE} & PHASE1_READ_TOOLS, "The external CLI's allowlist is unchanged")
        self.assertFalse({READ, FILE} & SCENE_FREE, "Both read scene data, so a job that locks Blender refuses them")
        self.assertLessEqual({READ, FILE}, COLLABORATOR_TOOLS, "A read-only collaborator may use them")

    def test_every_runtime_is_told_the_tools_exist(self):
        for text in (providers.INSTRUCTIONS, providers.codex_instructions(short_identifiers=True, deferred=True, plan=True)):
            # Facts-only text: what angelo_read_data reads, not when to use it.
            flat = " ".join(text.split())
            self.assertIn("angelo_read_data reads one path of the open file", flat)
            self.assertIn("read-only and without approval", flat)
            self.assertIn("Computed add-on getters require an approved script", text)
            self.assertIn("angelo_blend_file reports", text)
        # A Claude session reads the Claude per-model text (claude_text).
        text = providers.CLAUDE_INSTRUCTIONS
        self.assertIn("angelo_read_data for stored/default values", text)
        self.assertIn("(one path, no approval); never guess or script these reads", text)
        self.assertIn("Computed add-on getters require an approved script", text)
        self.assertIn("angelo_blend_file reports", text)
        self.assertIn("angelo_expand_recipe, angelo_blend_file) are deferred", providers.CODEX_TOOLS_GUIDANCE)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.controller = Controller(self.folder.name)
        self.controller.set_document(("instance", "scratch.blend", "Scene"))
        self.controller.busy = True
        self.calls = []

    def tearDown(self):
        self.controller.close()
        self.folder.cleanup()

    def precondition(self, name, arguments):
        raise AssertionError("A read asks for no approval, so nothing binds a precondition")

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return canned(name, arguments)

    def call(self, identity, name, arguments):
        answers = []
        self.controller.request_tool(identity, name, arguments, answers.append)
        self.controller.pump_tools(self, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        return answers

    def test_both_run_at_once_in_every_approval_mode_including_read_only(self):
        for mode in ("ASK", "AUTO_BOUNDED", "READ_ONLY"):
            self.controller.state["options"]["approval"] = mode
            for name, arguments in ((READ, {"path": "scene.cycles.feature_set"}), (FILE, {}), (FILE, {"file": "x.blend"})):
                with self.subTest(mode=mode, name=name, arguments=arguments):
                    (answer,) = self.call("%s-%s-%d" % (mode, name, len(self.calls)), name, arguments)
                    self.assertNotIn("error", answer)
                    self.assertEqual(self.calls[-1][0], name)
                    self.assertFalse(self.controller.pending, "No approval is left waiting")
                    self.assertFalse(self.controller.requires_mutation_checkpoint(name))
        self.assertIsNone(self.controller.request_checkpoint, "A read takes no snapshot")

    def test_defaults_are_applied_and_bad_shapes_run_nothing(self):
        self.call("defaults", READ, {"path": "scene"})
        self.assertEqual(self.calls, [(READ, {"path": "scene", "offset": 0, "limit": 32, "depth": 1})])
        # A path outside the grammar is admitted: the reader answers it, so it costs a read, not a rejected plan.
        (answer,) = self.call("grammar", READ, {"path": "scene.objects.get('Cube')"})
        self.assertEqual((answer["found"], answer["code"], self.calls.pop()[1]["path"]),
                         (False, "CALL", "scene.objects.get('Cube')"))
        for identity, arguments in (("limit", {"path": "scene", "limit": 500}), ("extra", {"path": "scene", "code": "1"}),
                                    ("missing", {})):
            (answer,) = self.call(identity, READ, arguments)
            self.assertIn("error", answer)
        self.assertEqual(len(self.calls), 1)


class ClaudeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.claude = ClaudeStandIn(canned)
        self.addCleanup(self.claude.close)

    def test_answers_reach_the_next_round_and_a_wrong_guess_does_not_end_the_turn(self):
        status = self.claude.run(
            "What are the sockets, and is anything unused?",
            plan((READ, {"path": 'data.node_groups["Rock"].nodes[0].inputs'}),
                 (READ, {"path": "scene.cycles.feature_set"}), (FILE, {}), (FILE, {"file": "/etc/passwd"})),
            plan(reply="The socket is Planted_Scale_7; Planted Orphan is unused."))
        self.assertEqual(status, "completed")
        self.assertEqual([call[0] for call in self.claude.dispatcher.calls], [READ, READ, FILE, FILE, "angelo_inspect_scene"])
        self.assertEqual(len(self.claude.prompts), 2, "One round of reads, one answer")
        results = self.claude.prompts[1]
        for planted in ("Planted_Scale_7", '"found":false', '"members":["samples","device"]', "Planted Orphan",
                        '"status":"missing"', "OUTSIDE_WORKSPACE"):
            self.assertIn(planted, results)
        self.assertFalse([row for row in self.claude.system_rows() if "rejected" in row or "stopped" in row])

    def test_no_answer_shape_reads_as_a_failed_operation(self):
        for answer in (SOCKETS, UNKNOWN, SUMMARY, OUTSIDE):
            self.assertIsNone(native_result_problem(answer))
            self.assertIsNone(native_result_problem({"success": True, "operations": [answer]}))


class CodexDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.codex = CodexStandIn(canned)
        self.addCleanup(self.codex.close)

    def test_answers_reach_the_model_inside_the_turn(self):
        status = self.codex.run("What are the sockets, and is anything unused?", [
            ("tool", READ, {"path": 'data.node_groups["Rock"].nodes[0].inputs'}),
            ("tool", READ, {"path": "scene.cycles.feature_set"}),
            ("tool", FILE, {}, CODEX_DEFERRED_NAMESPACE),
            ("tool", FILE, {"file": "/etc/passwd"}, CODEX_DEFERRED_NAMESPACE),
            final("The socket is Planted_Scale_7; Planted Orphan is unused.")])
        self.assertEqual(status, "completed")
        answers = self.codex.answers()
        self.assertEqual([success for success, _answer in answers], [True] * 4, "No answer is a failed tool call")
        self.assertEqual(answers[0][1]["items"][0]["identifier"], "Planted_Scale_7")
        self.assertEqual((answers[1][1]["found"], answers[1][1]["members"]), (False, ["samples", "device"]))
        self.assertEqual(answers[2][1]["unused"]["items"], [{"type": "meshes", "name": "Planted Orphan"}])
        self.assertEqual(answers[3][1]["code"], "OUTSIDE_WORKSPACE")
        self.assertEqual(json.loads(self.codex.answer_texts()[0]), answers[0][1])


if __name__ == "__main__":
    unittest.main()
