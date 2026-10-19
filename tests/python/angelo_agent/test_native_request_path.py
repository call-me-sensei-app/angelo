# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A receipt says how the agent got to its result: pictures per provider turn, edits, lookups and attempts.

The unit cases feed ``request_path.PathFacts`` the result shapes the host
returns. The receipt cases run a real Controller with each runtime's stand-in
(the Claude stdio fixture, the scripted Codex app-server) and read only what a
test harness may read: ``controller.telemetry``. No model, process or Blender.
"""

import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import NativeInitializeFixture, SUPPORTED
from angelo_agent import job_manager
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.job_manager import JobManager
from angelo_agent.job_store import JobStore
from angelo_agent.request_path import MAX_IMAGE_TURNS, MAX_STEPS, PathFacts, clean_path
from angelo_agent.telemetry import clean_receipt
from angelo_agent.turn_context import TurnContext
from test_native_codex_plan import PlanTurnFixture, ScriptedAppServer
from angelo_agent.protocol import RpcError
from test_native_long_jobs import FakeRunner
from test_native_provider_usage import claude_result, codex_breakdown, token_update
from test_native_round_savings import committed, program
from test_native_turn_activity import CLAUDE, OfflineClaude, message_delta, message_start

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
SCRIPT = {"description": "Bevel the deck", "code": "result = 1"}
SCRIPT_OK = {"success": True, "result": 1, "output": "", "native_dispatch_wait_ms": 1.0}
RAISED = {"error": "KeyError: 'Scale' (line 2)", "script_error": True, "rolled_back": True, "scene_changed": False,
          "partial_changes_possible": False, "native_dispatch_wait_ms": 1.0}
BEVEL = {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [], "operations": [
    {"kind": "mesh_bevel", "target": "scn:" + "a" * 32 + ":7", "parameters": {}}]}
REFUSED = {"error": "Native commit failed; live state rolled back: mesh_bevel UNSUPPORTED", "scene_changed": False,
           "partial_changes_possible": False, "success": False, "native_dispatch_wait_ms": 1.0}
INVALID = {"error": "Unknown operation parameter", "exact_schemas": {"ACTIONS": {}}}
VIEW_ARGUMENTS = {"targets": [], "views": ["FRONT"], "shading": "SOLID", "resolution": 512}
RENDER_ARGUMENTS = {"targets": [], "views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}


def view(identity, shading="SOLID", framing="AXIS", name="FRONT", **extra):
    return dict({"id": identity, "view_id": identity, "mime": "image/png", "kind": "image", "view": name,
                 "framing": framing, "shading": shading}, **extra)


def view_result(*records, **extra):
    return dict({"success": True, "read_only": True, "scene_changed": False, "views": list(records)}, **extra)


class PictureTests(unittest.TestCase):
    """Which pictures reached the model in which provider turn, and after how many edits."""

    def test_a_picture_counts_for_the_turn_whose_message_the_provider_accepted(self):
        path = PathFacts()
        path.pictures([{"id": "a1", "mime": "image/png"}, {"id": "notes", "mime": "text/plain"}], staged=True)
        self.assertNotIn("images", path.snapshot() or {}, "Handed over, not accepted yet")
        path.accepted()
        path.tool("angelo_scene_program", program()["arguments"], committed())
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("v1"), view("v2", "MATERIAL", "SCENE_CAMERA", "SCENE_CAMERA")))
        path.pictures([view("v1"), view("v2")], staged=True)
        path.accepted()
        self.assertEqual(path.snapshot(), {
            "edits": 1, "image_delivery": "confirmed", "images_returned": 2, "images_delivered": 3,
            "images": [[1, None, {"attachment": 1}], [2, 1, {"view:SOLID:AXIS": 1, "view:MATERIAL:SCENE_CAMERA": 1}]],
            "steps": [["typed", "ok", "angelo_scene_program", ["create_primitive"], 1]]})

    def test_a_message_that_was_never_accepted_delivers_nothing(self):
        path = PathFacts()
        path.accepted()
        path.pictures([{"id": "a1", "mime": "image/png"}], staged=True)  # The write failed; the next round stages again.
        path.pictures([{"id": "a2", "mime": "image/png"}], staged=True)
        path.accepted()
        self.assertEqual(path.snapshot()["images"], [[2, None, {"attachment": 1}]])

    def test_pictures_inside_a_turn_keep_their_individual_capture_edits(self):
        path = PathFacts()  # One Codex turn: look, edit, look again.
        path.accepted()
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("v1")))
        path.pictures([view("v1")], staged=False)
        self.assertEqual(path.snapshot()["images"], [[1, 0, {"view:SOLID:AXIS": 1}]])
        path.tool("angelo_execute_python", SCRIPT, SCRIPT_OK)
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("v3")))
        path.pictures([view("v3")], staged=False)
        snapshot = path.snapshot()
        self.assertEqual(snapshot["images"], [[1, 0, {"view:SOLID:AXIS": 1}], [1, 1, {"view:SOLID:AXIS": 1}]])
        self.assertEqual(snapshot["images"][-1][1], snapshot["edits"], "The model looked after its last edit")

    def test_kinds_are_closed_names(self):
        path = PathFacts()
        path.accepted()
        path.tool("angelo_sculpt_view", {}, view_result({"id": "s1", "mime": "image/png", "view": "front"}))
        path.tool("angelo_scene_program", BEVEL, dict(committed(), views=[{"id": "p1", "mime": "image/png"}]))
        path.tool("angelo_studio_generate", {}, view_result({"id": "m1", "mime": "image/png"}))
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("odd", "Secret Shading!", "x y")))
        path.pictures([{"id": key, "mime": "image/png"} for key in ("s1", "p1", "m1", "odd")], staged=False)
        self.assertEqual(path.snapshot()["images"], [[1, 0, {"sculpt": 1}], [1, 1, {"preview": 1, "media": 1,
                                                             "view:UNKNOWN:UNKNOWN": 1}]])

    def test_delayed_delivery_does_not_refresh_the_capture_watermark(self):
        path = PathFacts()
        path.accepted()
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("old")))
        path.tool("angelo_execute_python", SCRIPT, SCRIPT_OK)
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(view("new")))
        path.pictures([view("old"), view("new")], staged=True)
        path.accepted()
        self.assertEqual(path.snapshot()["images"], [[2, 0, {"view:SOLID:AXIS": 1}], [2, 1, {"view:SOLID:AXIS": 1}]])

    def test_a_jobs_picture_is_named_by_its_job(self):
        # The shape a view of a finished render returns: the job in ``rendered``, or in the view record itself.
        path = PathFacts()
        path.accepted()
        path.tool("angelo_view", {}, view_result(view("r1", "RENDERED", "FINISHED_IMAGE"), rendered={"job": "j3"}))
        path.tool("angelo_view", {}, view_result(view("r2", "RENDERED", "FINISHED_IMAGE", job="j12")))
        self.assertEqual(path.pictures([view("r1"), view("r2")], staged=False), ["j3", "j12"])
        self.assertEqual(path.snapshot()["images"], [[1, None, {"job:j3": 1, "job:j12": 1}]])
        path.tool("angelo_view", {}, view_result(view("r3"), rendered={"job": "../../etc"}))
        path.pictures([view("r3")], staged=False)
        self.assertIn("view:SOLID:AXIS", path.snapshot()["images"][-1][2], "An odd job name is not a job")

    def test_existing_picture_sources_never_acquire_a_current_scene_capture_age(self):
        for source in ("j1", "RENDER_RESULT", "renders/old.png"):
            with self.subTest(source=source):
                path = PathFacts()
                path.accepted()
                path.tool("angelo_execute_python", SCRIPT, SCRIPT_OK)
                picture = view("existing", "RENDERED", "FINISHED_IMAGE")
                path.tool("angelo_view", {"image": source}, view_result(picture))
                path.pictures([picture], staged=False)
                self.assertIsNone(path.snapshot()["images"][0][1])

    def test_editor_captures_sent_are_counted_and_survive_a_reload(self):
        path = PathFacts()
        path.tool("angelo_view", {"image": "EDITOR"}, view_result(view("e1", "RENDERED", "EDITOR")))
        path.tool("angelo_view", {"image": "EDITOR:UV"}, view_result(view("e2", "RENDERED", "EDITOR")))
        path.tool("angelo_view", {"image": "IMAGE:Albedo"}, view_result(view("i1", "RENDERED", "IMAGE_DATA")))
        path.tool("angelo_view", {"image": "EDITOR"}, view_result(view("e3", "RENDERED", "EDITOR")),
                  include_images=False)  # Not sent: not counted.
        receipt = path.snapshot()
        self.assertEqual((receipt["images_returned"], receipt["editor_captures"]), (3, 2))
        self.assertEqual(clean_path(receipt)["editor_captures"], 2)
        self.assertNotIn("editor_captures", clean_path(dict(receipt, editor_captures=-1)))
        plain = PathFacts()
        plain.tool("angelo_view", {}, view_result(view("v1")))
        self.assertNotIn("editor_captures", plain.snapshot())

    def test_the_latest_turns_are_kept_and_the_total_still_counts_every_picture(self):
        path = PathFacts()
        for _ in range(MAX_IMAGE_TURNS + 5):
            path.pictures([{"id": "a", "mime": "image/png"}], staged=True)
            path.accepted()
        snapshot = path.snapshot()
        self.assertEqual((snapshot["images_delivered"], len(snapshot["images"])), (MAX_IMAGE_TURNS + 5, MAX_IMAGE_TURNS))
        self.assertEqual(snapshot["images"][-1][0], MAX_IMAGE_TURNS + 5, "The last turns say whether the result was looked at")


class StepTests(unittest.TestCase):
    """Why a script: what was looked up or attempted before it in the same request, and how that went."""

    def steps(self, *calls):
        path = PathFacts()
        path.accepted()
        for name, arguments, result in calls:
            path.tool(name, arguments, result)
        return path.snapshot().get("steps", [])

    def test_tried_typed_and_was_refused_reads_differently_from_straight_to_a_script(self):
        refused = self.steps(("angelo_agent_capabilities", {"family": "ACTIONS", "names": ["mesh_bevel", "mesh_inset"]}, {"actions": []}),
                             ("angelo_scene_program", BEVEL, copy.deepcopy(INVALID)),
                             ("angelo_scene_program", BEVEL, copy.deepcopy(REFUSED)),
                             ("angelo_execute_python", SCRIPT, copy.deepcopy(SCRIPT_OK)))
        self.assertEqual(refused, [["lookup", "ok", "ACTIONS", ["mesh_bevel", "mesh_inset"], 1],
                                   ["typed", "invalid", "angelo_scene_program", ["mesh_bevel"], 1],
                                   ["typed", "other", "angelo_scene_program", ["mesh_bevel"], 1],
                                   ["script", "ok", "", [], 1]])
        straight = self.steps(("angelo_execute_python", SCRIPT, copy.deepcopy(SCRIPT_OK)))
        self.assertEqual(straight, [["script", "ok", "", [], 1]])

    def test_api_lookups_declines_and_raised_scripts_are_told_apart(self):
        steps = self.steps(("angelo_blender_api", {"domain": "RNA", "name": "CyclesRenderSettings", "member": ""}, {"items": []}),
                           ("angelo_execute_python", SCRIPT, dict(RAISED)),
                           ("angelo_execute_python", SCRIPT, {"error": "The user declined this operation. Do not repeat it.",
                                                              "native_dispatch_wait_ms": 1.0}),
                           ("angelo_job_start", {"kind": "RENDER_STILL", "output": "renders/final.png"},
                            {"error": "Blender operation/approval expired", "expired": True}))
        self.assertEqual(steps, [["api", "ok", "RNA", [], 1], ["script", "raised", "", [], 1],
                                 ["script", "declined", "", [], 1], ["typed", "expired", "angelo_job_start", ["RENDER_STILL"], 1]])
        self.assertNotIn("CyclesRenderSettings", json.dumps(steps), "A looked-up API name is the model's text")
        self.assertNotIn("final.png", json.dumps(steps))

    def test_repeats_fold_and_the_trail_is_bounded(self):
        calls = [("angelo_execute_python", SCRIPT, copy.deepcopy(SCRIPT_OK))] * 7
        self.assertEqual(self.steps(*calls), [["script", "ok", "", [], 7]])
        path = PathFacts()
        for index in range(MAX_STEPS + 3):
            path.tool("angelo_execute_python", SCRIPT, copy.deepcopy(SCRIPT_OK if index % 2 else RAISED))
        snapshot = path.snapshot()
        self.assertEqual((len(snapshot["steps"]), snapshot["steps_dropped"]), (MAX_STEPS, 3))

    def test_reads_notes_and_files_are_not_steps_and_do_not_edit_the_scene(self):
        path = PathFacts()
        path.accepted()
        for name in ("angelo_inspect_scene", "angelo_scene_search", "angelo_memory_update", "angelo_write_file"):
            path.tool(name, {}, {"success": True, "native_dispatch_wait_ms": 1.0})
        self.assertEqual(path.snapshot(), {"edits": 0})
        path.tool("angelo_scene_program", dict(BEVEL, mode="PREVIEW"), {"success": True, "status": "preview", "scene_changed": False})
        path.tool("angelo_execute_python", SCRIPT, dict(RAISED))  # Rolled back: the scene is as it was.
        path.tool("angelo_execute_python", SCRIPT, {"error": "x", "script_error": True, "partial_changes_possible": True})
        self.assertEqual(path.snapshot()["edits"], 1, "Only the script whose changes stayed")

    def test_a_plan_the_host_returned_names_the_operations_it_refused(self):
        path = PathFacts()
        path.accepted()
        operations = [{"tool": "angelo_scene_search", "arguments": {}}, {"tool": "angelo_scene_program", "arguments": BEVEL},
                      {"tool": "angelo_execute_python", "arguments": {"description": "", "code": "x"}}]
        path.refused(operations, ["operations[1] (angelo_scene_program): Unknown operation parameter",
                                  "operations[2] (angelo_execute_python): Describe the script's changes in 1-500 printable "
                                  "characters (this one has 0)", "operations[9] (x): out of range"])
        self.assertEqual(path.snapshot()["steps"], [["typed", "invalid", "angelo_scene_program", ["mesh_bevel"], 1],
                                                     ["script", "invalid", "", [], 1]])


class AllowlistTests(unittest.TestCase):
    def test_old_delivery_clock_receipts_reload_with_unknown_capture_age(self):
        old = {"edits": 3, "images_delivered": 1, "images": [[2, 3, {"view:SOLID:AXIS": 1}]]}
        restored = clean_path(old)
        self.assertEqual(restored["unconfirmed_images"], [[2, None, {"view:SOLID:AXIS": 1}]])
        self.assertEqual(restored["image_delivery"], "unknown")
        self.assertEqual(restored["images_unconfirmed"], 1)
        self.assertNotIn("images_delivered", restored)
        self.assertEqual(clean_path(restored), restored)

    def test_unconfirmed_only_receipts_are_unknown_to_current_and_reloaded_consumers(self):
        path = PathFacts()
        path.accepted()
        picture = view("sent")
        path.tool("angelo_view", VIEW_ARGUMENTS, view_result(picture))
        path.unconfirmed([picture])
        raw = path.snapshot()
        self.assertEqual((raw["images_returned"], raw["images_unconfirmed"], raw["image_delivery"]), (1, 1, "unknown"))
        self.assertNotIn("images_delivered", raw)
        self.assertEqual(clean_path(raw), raw)
        # A copied/old producer's unsupported positive label cannot turn an
        # unconfirmed-only receipt into positive or negative evidence.
        self.assertEqual(clean_path(dict(raw, image_delivery="confirmed"))["image_delivery"], "unknown")

    def test_identifier_shaped_content_is_removed_live_and_after_reload(self):
        planted = "ClientAcme_Logo"
        path = PathFacts()
        for name, arguments in (("angelo_agent_capabilities", {"family": "ACTIONS", "names": [planted, "mesh_bevel"]}),
                                ("angelo_agent_capabilities", {"family": planted, "names": ["mesh_bevel"]}),
                                ("angelo_blender_api", {"domain": planted}),
                                ("angelo_scene_program", {"operations": [{"kind": planted}]}),
                                ("angelo_job_start", {"kind": planted}),
                                ("angelo_apply_recipe", {"recipe": planted})):
            path.tool(name, arguments, INVALID)
        path.tool("angelo_view", {}, view_result(view("x", "ACME", "LOGO")))
        path.pictures([view("x")], staged=False)
        self.assertNotIn(planted, json.dumps(path.snapshot()))
        dirty = {"edits": 0, "images_delivered": 1, "images": [[1, 0, {"view:ACME:LOGO": 1}]],
                 "steps": [["lookup", "ok", "ACTIONS", [planted, "mesh_bevel"], 1],
                           ["lookup", "ok", planted, [], 1], ["typed", "invalid", "angelo_job_start", [planted], 1],
                           ["api", "invalid", planted, [], 1]]}
        clean = clean_path(dirty)
        self.assertNotIn(planted, json.dumps(clean))
        self.assertNotIn("images", clean)
        self.assertEqual(clean["steps"][0][3], ["mesh_bevel"])

    def test_closed_check_variants_recipes_and_batch_tools_are_retained(self):
        path = PathFacts()
        path.tool("angelo_agent_capabilities", {"family": "CHECKS", "names": ["modifier", "modifier:BEVEL", "modifier:SECRET"]}, {})
        path.tool("angelo_apply_recipe", {"recipe": "bevel-and-shade"}, INVALID)
        path.tool("angelo_apply_batch", {"operations": [{"tool": "angelo_create_cube"}, {"tool": "secret_tool"}]}, INVALID)
        steps = path.snapshot()["steps"]
        self.assertEqual([step[3] for step in steps], [["modifier", "modifier:BEVEL"], ["bevel-and-shade"], ["angelo_create_cube"]])
        self.assertEqual(clean_path(path.snapshot()), path.snapshot())

    def test_a_recorded_path_survives_the_receipt_allowlist_unchanged(self):
        path = PathFacts()
        path.pictures([{"id": "a", "mime": "image/png"}], staged=True)
        path.accepted()
        path.tool("angelo_agent_capabilities", {"family": "ACTIONS", "names": ["mesh_bevel"]}, {"actions": []})
        path.tool("angelo_execute_python", SCRIPT, copy.deepcopy(SCRIPT_OK))
        receipt = clean_receipt({"status": "completed", "runtime": "codex", "phases": {"total_ms": 9}, "counts": {},
                                 "path": path.snapshot()})
        self.assertEqual(receipt["path"], path.snapshot())
        self.assertEqual(json.loads(json.dumps(receipt))["path"], receipt["path"])

    def test_free_text_cannot_become_receipt_data(self):
        dirty = {"edits": 2, "images_delivered": 2, "note": "Secret", "images": [
            [1, 0, {"attachment": 1, "view:SOLID:My Secret Camera": 1, "job:../x": 1}], [2, "x", {"attachment": 1}], "junk"],
            "steps": [["typed", "ok", "angelo_scene_program", ["mesh_bevel", "My Secret Object", 7], 1],
                      ["script", "because the user said so", "", [], 1], ["typed", "ok", "a tool with spaces", [], 1],
                      ["lookup", "ok", "ACTIONS", [], "many"]], "steps_dropped": "lots"}
        self.assertEqual(clean_path(dirty), {
            "edits": 2, "image_delivery": "unknown", "images_unconfirmed": 2,
            "unconfirmed_images": [[1, None, {"attachment": 1}]],
            "steps": [["typed", "ok", "angelo_scene_program", ["mesh_bevel"], 1]]})
        self.assertNotIn("Secret", json.dumps(clean_path(dirty)))
        for value in (None, [], {"edits": -1}, {"edits": "3"}, {"images": []}):
            self.assertIsNone(clean_path(value))
        base = {"status": "completed", "runtime": "claude", "phases": {"total_ms": 9}, "counts": {}}
        self.assertNotIn("path", clean_receipt(dict(base, path={"edits": "many"})))


class ClaudeReceiptTests(unittest.TestCase):
    """A real Controller and Claude provider: an edit, a look at it, and the round that says it is done."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("scratch", 1))
        self.c.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.c.state["options"]["approval"] = "AUTO_BOUNDED"
        self.rounds, self.plans, self.hold = 0, [], None
        self.reject_round, self.reject_after_start, self.input_images = None, False, []
        for value in (patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                      patch("angelo_agent.child_process.popen",
                            side_effect=lambda *args, **kwargs: NativeInitializeFixture(on_user=self.reply)),
                      patch("angelo_agent.child_process.run",
                            return_value=SimpleNamespace(returncode=0, stdout=SUPPORTED)),
                      patch("angelo_agent.turn_context.prepare", return_value=TurnContext(
                          {"context_version": 2, "objects": []}, stats={}))):
            value.start()
            self.addCleanup(value.stop)

    def reply(self, process, event):
        self.rounds += 1
        index = self.rounds
        content = event["message"]["content"]
        self.input_images.append(sum(item.get("type") == "image" for item in content) if isinstance(content, list) else 0)
        if index == self.reject_round:
            if self.reject_after_start:
                process.stdout.emit(message_start("msg_%d" % index, 100 * index, 0, 3000))
            result = claude_result(0, 0, 0, 0, api_ms=0, cost=0, subtype="error_during_execution")
            result.update(is_error=True, errors=["API rejected the input"])
            process.stdout.emit(result)
            return
        process.stdout.emit(message_start("msg_%d" % index, 100 * index, 0, 3000))
        if self.hold is not None and index == self.hold[0]:
            self.hold[1].set()  # This round thinks until the test lets it go.
            return
        process.stdout.emit(message_delta(200))
        result = claude_result(100 * index, 0, 3000 * index, 200 * index, api_ms=2500 * index, cost=.04 * index)
        result["structured_output"] = self.plans[index - 1]
        process.stdout.emit(result)

    def execute(self, name, arguments):
        if name != "angelo_view":
            return committed()
        reference = self.c.references.new_observation("Front view · solid")
        Path(reference["path"]).write_bytes(PNG)
        shading = arguments.get("shading", "SOLID")
        return view_result(dict(reference, view_id=reference["id"], view="FRONT", framing="AXIS", shading=shading,
                                width=8, height=8, camera={}, cache_hit=False), shading=shading,
                           **({"rendered": {"job": arguments["image"]}} if "image" in arguments else {}))

    def run_request(self, text="Create a cube and look at it", until=None):
        executor = SimpleNamespace(calls=[], window="window", supports_native_job=lambda name: False,
                                   precondition=lambda name, arguments: "unchanged", execute=self.execute)
        self.c.send(text, {"objects": []})
        apply = lambda call: self.c.execute_tool(call, executor, self.c.document)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.c.pump_events()
            self.c.pump_tools(executor, self.c.document, apply_mutation=apply, budget=ToolDispatchBudget(seconds=60))
            if until is not None and until():
                return
            if until is None and not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                return
            time.sleep(.002)
        self.fail("The request did not finish")

    def test_the_receipt_says_which_round_saw_the_view_taken_after_the_edit(self):
        self.plans = [{"reply": "", "operations": [program(), {"tool": "angelo_view", "arguments": VIEW_ARGUMENTS}], "verify": True},
                      {"reply": "The cube is in place.", "operations": []}]
        self.run_request()
        receipt = self.c.telemetry.receipts[-1]
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["path"], {"edits": 1, "image_delivery": "confirmed", "images_returned": 1,
                                           "images_delivered": 1, "images": [[2, 1, {"view:SOLID:AXIS": 1}]],
                                           "steps": [["typed", "ok", "angelo_scene_program", ["create_primitive"], 1]]})
        self.assertEqual((receipt["counts"]["provider_turn_requests"], receipt["usage"]["requests"]), (2, 2))

    def test_a_plan_the_host_returned_is_a_refused_typed_step_before_the_script(self):
        bad = copy.deepcopy(program())
        bad["arguments"]["operations"][0]["parameters"]["no_such_parameter"] = 1
        self.plans = [{"reply": "", "operations": [bad]}, {"reply": "I cannot do that with a typed edit.", "operations": []}]
        self.run_request()
        self.assertEqual(self.c.telemetry.receipts[-1]["path"]["steps"],
                         [["typed", "invalid", "angelo_scene_program", ["create_primitive"], 1]])

    def test_rejected_first_input_never_marks_its_job_result_shown(self):
        record = self.finished_job()
        self.reject_round = 1
        self.run_request("Is the render finished?")
        self.assertEqual(self.c.telemetry.receipts[-1]["status"], "failed")
        self.assertEqual(record["shown"], {})
        self.assertEqual(JobStore(self.directory.name).get(record["id"])["shown"], {})

    def test_rejected_picture_input_preserves_only_the_earlier_accepted_result(self):
        record = self.finished_job()
        self.plans = [{"reply": "", "operations": [{"tool": "angelo_view", "arguments": dict(RENDER_ARGUMENTS, image=record["id"])}], "verify": True}]
        self.reject_round = 2
        self.run_request("Show the finished render")
        receipt = self.c.telemetry.receipts[-1]
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(self.input_images, [0, 1], "The rejected input really carried the picture")
        self.assertNotIn("images_delivered", receipt["path"])
        self.assertEqual(sorted(record["shown"]), ["result_at"])
        self.assertEqual(JobStore(self.directory.name).get(record["id"])["shown"], record["shown"])

    def test_acknowledged_pictures_remain_counted_when_later_output_fails(self):
        record = self.finished_job()
        self.plans = [{"reply": "", "operations": [{"tool": "angelo_view", "arguments": dict(RENDER_ARGUMENTS, image=record["id"])}], "verify": True}]
        self.reject_round, self.reject_after_start = 2, True
        self.run_request("Show the finished render")
        receipt = self.c.telemetry.receipts[-1]
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["path"]["images_delivered"], 1)
        self.assertEqual(record["shown"]["images"], 1)
        self.assertIn("result_at", record["shown"])

    def test_a_local_user_message_send_failure_leaves_job_results_unshown(self):
        record = self.finished_job()
        original = OfflineClaude._write
        def fail(provider, message, transport, **kwargs):
            if message.get("type") == "user":
                raise OSError("closed pipe")
            return original(provider, message, transport, **kwargs)
        with patch.object(OfflineClaude, "_write", fail):
            self.run_request("Is the render finished?")
        self.assertEqual(self.c.telemetry.receipts[-1]["status"], "failed")
        self.assertEqual(record["shown"], {})

    def test_an_earlier_look_in_a_plan_is_not_a_final_result_look(self):
        self.plans = [{"reply": "", "operations": [{"tool": "angelo_view", "arguments": RENDER_ARGUMENTS}, program()], "verify": True},
                      {"reply": "Done.", "operations": []}]
        self.run_request()
        path = self.c.telemetry.receipts[-1]["path"]
        self.assertEqual((path["edits"], path["images"]), (1, [[2, 0, {"view:RENDERED:AXIS": 1}]]))

    def test_usage_of_a_round_in_progress_can_be_read_without_private_attributes(self):
        self.plans = [{"reply": "", "operations": [program()], "verify": True}]
        self.hold = (2, threading.Event())
        self.assertIsNone(self.c.telemetry.running_usage(self.c.state["id"]), "No request yet")
        self.run_request(until=lambda: self.hold[1].is_set())
        deadline = time.monotonic() + 5
        usage = None
        while time.monotonic() < deadline:  # The reader delivers the second round's message_start.
            usage = self.c.telemetry.running_usage(self.c.state["id"])
            if usage and usage["running"]:
                break
            time.sleep(.005)
        self.assertEqual(usage, {"input_tokens": 3100 + 3200, "cached_input_tokens": 0, "cache_write_input_tokens": 6000,
                                 "output_tokens": 200, "cost_usd": .04, "requests": 2, "turns": 2, "running": True})
        self.assertIsNone(self.c.telemetry.running_usage("another-session"))
        self.c.stop()
        self.run_request_end()
        self.assertIsNone(self.c.telemetry.running_usage(self.c.state["id"]), "The request has its receipt")

    def finished_job(self):
        manager = JobManager(JobStore(self.directory.name))
        job_manager.install(manager)
        self.addCleanup(job_manager.install, None)
        runner = FakeRunner()
        record = manager.submit(self.c.state["id"], "call-1", {"kind": "RENDER_STILL", "output": "renders/final.png",
                                                               "minutes": 30.0}, runner)
        runner.ended = True
        manager.poll()
        return record

    def test_a_finished_job_is_marked_shown_once_the_turn_that_listed_it_ended(self):
        record = self.finished_job()
        self.plans = [{"reply": "The render is done: renders/final.png.", "operations": []}]
        self.run_request("Is the render finished?")
        self.assertEqual(sorted(record["shown"]), ["result_at"])
        self.assertEqual(JobStore(self.directory.name).get(record["id"])["shown"], record["shown"])

    def test_a_stopped_turn_does_not_claim_the_job_was_shown(self):
        record = self.finished_job()
        self.hold = (1, threading.Event())
        self.run_request("Is the render finished?", until=lambda: self.hold[1].is_set())
        self.c.stop()
        self.run_request_end()
        self.assertEqual(record["shown"], {}, "The runtime may not have recorded the message; it is listed again next time")

    def run_request_end(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (self.c.busy or (self.c._worker and self.c._worker.is_alive())):
            self.c.pump_events()
            time.sleep(.002)
        self.c.pump_events()


class CodexReceiptTests(PlanTurnFixture):
    """A real Controller and Codex provider over the scripted app-server, through the real request context."""

    def setUp(self):
        super().setUp()
        del self.context.request_tool  # The request context's own dispatch; the host's answer is the stand-in.
        def dispatch(key, name, arguments, callback, request_fence=None, delivery_owner=None, result_observer=None,
                     completion_sink=None):
            def complete(result):
                if result_observer is not None:
                    result_observer(result)
                callback(result)
            self.request_tool(key, name, arguments, complete)
        self.c.request_tool = dispatch
        self.p._usage.thread_started("native-thread")

    def view(self):
        reference = self.c.references.new_observation("Front view · solid")
        Path(reference["path"]).write_bytes(PNG)
        return view_result(dict(reference, view_id=reference["id"], view="SCENE_CAMERA", framing="SCENE_CAMERA",
                                shading="MATERIAL", width=8, height=8, camera={}, cache_hit=False), shading="MATERIAL")

    def update(self, prompt, output):
        return ("call", lambda: self.p._event("thread/tokenUsage/updated", token_update(
            "native-thread", self.p.turn_id, codex_breakdown(prompt, 0, output))))

    def finish(self):
        self.c._finish_metrics("completed")
        return self.c.telemetry.receipts[-1]

    def test_the_receipt_distinguishes_returned_views_from_unconfirmed_model_delivery(self):
        self.results.update(angelo_execute_python=[copy.deepcopy(SCRIPT_OK)], angelo_view=[self.view()])
        status = self.run_turns([self.update(1000, 40), ("tool", "angelo_execute_python", SCRIPT),
                                 self.update(2100, 90), ("tool", "angelo_view", VIEW_ARGUMENTS),
                                 self.update(3300, 130), ("message", '{"reply": "Bevelled and checked.", "operations": []}')])
        self.assertEqual(status, "completed")
        receipt = self.finish()
        self.assertEqual(receipt["path"], {"edits": 1, "image_delivery": "unknown", "images_returned": 1,
                                           "images_unconfirmed": 1, "unconfirmed_images": [[1, 1, {"view:MATERIAL:SCENE_CAMERA": 1}]],
                                           "steps": [["script", "ok", "", [], 1]]})
        self.assertEqual((receipt["usage"]["requests"], receipt["usage"]["input_tokens"]), (3, 3300))
        images = [item for _, payload in self.p.rpc.responses for item in payload["contentItems"] if item["type"] == "inputImage"]
        self.assertEqual(len(images), 1, "The runtime received a picture; forwarding to the model remains unconfirmed")

    def test_tried_typed_was_refused_then_a_script(self):
        self.results.update(angelo_agent_capabilities=[{"actions": []}], angelo_scene_program=[copy.deepcopy(REFUSED)],
                            angelo_execute_python=[dict(RAISED), copy.deepcopy(SCRIPT_OK)])
        self.run_turns([("tool", "angelo_agent_capabilities", {"family": "ACTIONS", "names": ["mesh_bevel"]}),
                        ("tool", "angelo_scene_program", BEVEL), ("tool", "angelo_execute_python", SCRIPT),
                        ("tool", "angelo_execute_python", SCRIPT),
                        ("message", '{"reply": "Bevelled with a script.", "operations": []}')])
        receipt = self.finish()
        self.assertEqual(receipt["path"]["steps"], [["lookup", "ok", "ACTIONS", ["mesh_bevel"], 1],
                                                    ["typed", "other", "angelo_scene_program", ["mesh_bevel"], 1],
                                                    ["script", "raised", "", [], 1], ["script", "ok", "", [], 1]])
        self.assertEqual(receipt["path"]["edits"], 1)

    def test_a_tool_result_send_failure_never_counts_its_picture(self):
        self.results["angelo_view"] = [self.view()]
        with patch.object(ScriptedAppServer, "respond", side_effect=OSError("closed pipe")):
            self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS),
                            ("message", '{"reply":"No picture received.","operations":[]}')])
        path = self.finish()["path"]
        self.assertNotIn("images_delivered", path)
        self.assertNotIn("images_unconfirmed", path)
        self.assertEqual((path["images_returned"], path["image_delivery"]), (1, "none"))

    def test_a_rejected_tool_picture_input_never_counts_as_delivered(self):
        self.results["angelo_view"] = [self.view()]
        def rejected(server, thread, turn, steps):
            while self.p.turn_id != turn:
                time.sleep(.001)
            self.p._request("picture", "item/tool/call", {"threadId": thread, "turnId": turn,
                "tool": "angelo_view", "arguments": VIEW_ARGUMENTS})
            self.p._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": "failed"}})
        with patch.object(ScriptedAppServer, "play", rejected), self.assertRaises(RpcError):
            self.run_turns([])
        self.assertEqual(len(self.p.rpc.responses), 1, "The local response was written, then rejected")
        self.assertNotIn("images_delivered", self.finish()["path"])

    def test_a_rejected_initial_input_never_counts_its_attachment(self):
        picture = self.view()["views"][0]
        self.context.turn_attachments = [picture]
        def rejected(server, thread, turn, steps):
            while self.p.turn_id != turn:
                time.sleep(.001)
            self.p._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": "failed"}})
        with patch.object(ScriptedAppServer, "play", rejected), self.assertRaises(RpcError):
            self.run_turns([])
        self.assertNotIn("images_delivered", self.finish().get("path", {}))

    def test_unconfirmed_images_keep_their_capture_edit_count(self):
        self.results.update(angelo_view=[self.view()], angelo_execute_python=[SCRIPT_OK])
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), ("tool", "angelo_execute_python", SCRIPT),
                        ("message", '{"reply":"Done.","operations":[]}')])
        path = self.finish()["path"]
        self.assertEqual((path["edits"], path["unconfirmed_images"]), (1, [[1, 0, {"view:MATERIAL:SCENE_CAMERA": 1}]]))
        self.assertEqual(path["image_delivery"], "unknown")

    def test_the_remainder_of_an_in_flight_response_does_not_acknowledge_a_picture(self):
        self.results["angelo_view"] = [self.view()]
        def response_item():
            self.p._event("item/started", {"threadId": self.p.thread_id, "turnId": self.p.turn_id,
                "item": {"id": "old-reasoning", "type": "reasoning"}})
        snapshots = []
        self.run_turns([("call", response_item), ("tool", "angelo_view", VIEW_ARGUMENTS),
                        ("call", response_item), ("call", lambda: snapshots.append(self.c._request_metrics.path.snapshot())),
                        ("message", '{"reply":"I received the image.","operations":[]}')])
        self.assertNotIn("images_delivered", snapshots[0])
        path = self.finish()["path"]
        self.assertEqual(path["images_unconfirmed"], 1)
        self.assertNotIn("images_delivered", path)

    def test_a_successful_send_racing_reader_output_stays_recorded_as_unconfirmed(self):
        self.results["angelo_view"] = [self.view()]
        original = ScriptedAppServer.respond
        readers = []
        def raced(server, identity, payload):
            original(server, identity, payload)
            reader = threading.Thread(target=lambda: self.p._event("item/agentMessage/delta", {
                "threadId": self.p.thread_id, "turnId": self.p.turn_id, "itemId": "race", "delta": "Output"}))
            readers.append(reader)
            reader.start()
            reader.join(.1)
        with patch.object(ScriptedAppServer, "respond", raced):
            self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS)])
        for reader in readers:
            reader.join(1)
            self.assertFalse(reader.is_alive())
        path = self.finish()["path"]
        self.assertEqual((path["images_unconfirmed"], path["image_delivery"]), (1, "unknown"))
        self.assertNotIn("images_delivered", path)

    def test_a_send_failure_after_raced_output_never_counts_the_image(self):
        self.results["angelo_view"] = [self.view()]
        def raced_failure(server, identity, payload):
            self.p._event("item/agentMessage/delta", {"threadId": self.p.thread_id, "turnId": self.p.turn_id,
                "itemId": "unrelated-output", "delta": "Output"})
            raise OSError("flush failed")
        with patch.object(ScriptedAppServer, "respond", raced_failure):
            self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS)])
        path = self.finish()["path"]
        self.assertNotIn("images_delivered", path)
        self.assertNotIn("images_unconfirmed", path)

    def test_output_and_search_activity_do_not_mark_a_job_picture_shown(self):
        manager = JobManager(JobStore(self.temp.name))
        job_manager.install(manager)
        self.addCleanup(job_manager.install, None)
        runner = FakeRunner()
        record = manager.submit(self.c.state["id"], "call-1", {"kind": "RENDER_STILL", "output": "renders/final.png",
                                                               "minutes": 30.0}, runner)
        runner.ended = True
        manager.poll()
        result = self.view()
        result["rendered"] = {"job": record["id"]}
        self.results["angelo_view"] = [result]
        def output(kind):
            self.p._event("item/started", {"threadId": self.p.thread_id, "turnId": self.p.turn_id,
                "item": {"id": kind, "type": kind}})
        self.run_turns([("tool", "angelo_view", VIEW_ARGUMENTS), ("call", lambda: output("reasoning")),
                        ("call", lambda: output("webSearch")), ("message", '{"reply":"Done.","operations":[]}')])
        path = self.finish()["path"]
        self.assertEqual((path["image_delivery"], path["unconfirmed_images"][0][1]), ("unknown", None))
        self.assertEqual(record["shown"], {}, "Missing marks mean unestablished delivery, not a negative")

    def test_first_message_attachments_still_have_direct_model_input_acknowledgement(self):
        self.context.turn_attachments = [self.view()["views"][0]]
        self.run_turns([("message", '{"reply":"Received the attachment.","operations":[]}')])
        path = self.finish()["path"]
        self.assertEqual((path["images_delivered"], path["image_delivery"]), (1, "confirmed"))
        self.assertEqual(path["images"], [[1, None, {"attachment": 1}]])

    def test_usage_of_a_turn_in_progress_can_be_read_without_private_attributes(self):
        seen = []

        def read():
            try:
                seen.append(self.c.telemetry.running_usage(self.c.state["id"]))
            except Exception as error:  # Never leave the scripted turn waiting for its end.
                seen.append(error)
        self.run_turns([("call", read), self.update(9000, 40), self.update(19000, 90), ("call", read),
                        ("message", '{"reply": "Done.", "operations": []}')])
        self.assertIsNone(seen[0], "Nothing known before the first model response")
        self.assertEqual(seen[1], {"input_tokens": 19000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                   "output_tokens": 90, "reasoning_output_tokens": 0, "requests": 2, "turns": 1,
                                   "running": True})
        after = self.c.telemetry.running_usage(self.c.state["id"])
        self.assertEqual((after["input_tokens"], after["requests"], after["running"]), (19000, 2, False))


if __name__ == "__main__":
    unittest.main()
