# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-shot rules on both sides: one message, approvals, the cost and time caps, the saved still.

Stand-ins replace the chat controller, the client streams and Blender; no
process, account or model is involved.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_scenario as mcp_scenario  # noqa: E402
import oneshot_checks  # noqa: E402
import oneshot_mcp  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import oneshot_session  # noqa: E402
import scenario_oneshot_footbridge as footbridge  # noqa: E402
import scenario_session  # noqa: E402
import scenario_specs as specs  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (2560).to_bytes(4, "big") + (1440).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
# 1,000,000 fresh prompt tokens and 100,000 output tokens of GPT-6 Astra at list prices: 10 + 5 dollars.
ASTRA_USAGE = {"input_tokens": 1_000_000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
               "output_tokens": 100_000}


class Controller:
    """The slice of the chat controller the one-shot session reads."""

    def __init__(self):
        self.state = {"messages": [{"role": "system", "text": "earlier row"}]}
        self.sent, self.stops, self.busy = [], 0, True
        self.attachments, self.references = [], NS(import_images=lambda paths, existing: [])
        self._request_metrics, self.provider, self.turn_options = NS(usage={}), None, {}

    def send(self, text, observation=None):
        self.sent.append(text)
        self.state["messages"].append({"role": "user", "text": text})

    def stop(self):
        self.stops += 1
        self.busy = False


def session(cost_cap=None, model="gpt-6-astra"):
    environment = {"ANGELO_EVAL_MODEL": model, **({oneshot_session.COST_CAP_ENV: str(cost_cap)} if cost_cap else {})}
    with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, environment):
        return scenario_session.for_trial("oneshot-footbridge", Path(folder))


class OneMessageTests(unittest.TestCase):
    def test_angelo_driver_cannot_send_a_second_message(self):
        one, controller = session(), Controller()
        task = dict(specs.tasks("oneshot_footbridge")[0], references=[])
        one.attach(controller, task, {})
        controller.send("the brief")
        with self.assertRaisesRegex(RuntimeError, "exactly one message"):
            controller.send("continue")                           # Whoever calls it: the driver, a retry, a helper.
        with self.assertRaisesRegex(RuntimeError, "exactly one message"):
            controller.send("the brief")
        self.assertEqual(controller.sent, ["the brief"])
        self.assertEqual((one.state["messages"], one.state["refused"]), (1, 2))
        one.attach(controller, task, {})                          # Attaching again never re-arms the chat.
        with self.assertRaises(RuntimeError):
            controller.send("again")
        self.assertEqual(len(controller.sent), 1)

    def test_the_task_list_gives_the_driver_one_message_to_send(self):
        for task_set in specs.ONESHOT_TASK_SETS:
            self.assertEqual(len(specs.tasks(specs.scenario_id(task_set))), 1)

    def stream(self, slices=()):
        calls = {"sent": [], "interrupts": 0, "slices": list(slices)}

        def collect(deadline, task):
            if calls["slices"]:
                return calls["slices"].pop(0)
            return [], None, "deadline"
        stream = NS(send=lambda text, images=(): calls["sent"].append((text, list(images))), collect=collect,
                    interrupt=lambda: calls.__setitem__("interrupts", calls["interrupts"] + 1))
        return stream, calls

    def config(self, folder, **extra):
        return dict({"task_set": "oneshot-footbridge", "runtime": "claude", "model": "gpt-6-astra",
                     "claude_cwd": str(Path(folder) / "cwd")}, **extra)

    def test_mcp_driver_cannot_send_a_second_message(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as folder:
                reference = Path(folder, "hero.jpg")
                reference.write_bytes(b"jpeg bytes")
                stream, calls = self.stream()
                output = {}
                mcp_scenario.guard(stream, self.config(folder, runtime=runtime), Path(folder), output)
                stream.send("the brief", [str(reference)])
                with self.assertRaisesRegex(RuntimeError, "exactly one message"):
                    stream.send("continue")
                self.assertEqual(len(calls["sent"]), 1)
                state = output["oneshot"]
                self.assertEqual((state["messages"], state["refused_sends"]), (1, 1))
                self.assertLessEqual(abs(state["sent_wall"] - time.time()), 5)
                copy, = calls["sent"][0][1]
                self.assertEqual(Path(copy).parent, Path(folder) / "cwd" / "references")   # In the client's own folder,
                self.assertEqual(Path(copy).read_bytes(), b"jpeg bytes")                   # the same bytes.
                self.assertEqual(state["allowances"], oneshot_mcp.ALLOWANCES[runtime])



class SelfStartedContinuationTests(unittest.TestCase):
    """BENCH-PREREG Amendment J: a turn Angelo starts on its own (``host_request``, no user text) is part of the one shot;
    any other send after the brief is refused, the same for every arm and model."""

    def guarded(self):
        sent, state = [], {"messages": 0}
        return oneshot_session.once(lambda *args, **kwargs: sent.append((args, kwargs)), state), sent, state

    def test_the_brief_passes_and_a_typed_second_message_is_refused(self):
        send, sent, state = self.guarded()
        send("the brief")
        with self.assertRaisesRegex(RuntimeError, "exactly one message"):
            send("continue")
        with self.assertRaisesRegex(RuntimeError, "exactly one message"):
            send("continue", host_request=None)   # An explicit None is a user's send, never Angelo's own.
        self.assertEqual((len(sent), state["messages"], state["refused"]), (1, 1, 2))
        self.assertNotIn("continuations", state)

    def test_angelos_own_continuation_is_part_of_the_one_shot_and_counted(self):
        send, sent, state = self.guarded()
        send("the brief")
        send("Workers 2 and 3 ended.", observation=None, recovery_evidence={}, host_request=["2 'Bank'", "3 'Inn'"])
        send("Worker 4 ended.", host_request=["4 'Library'"])
        self.assertEqual(len(sent), 3)
        self.assertEqual((state["messages"], state.get("refused", 0)), (1, 0))
        self.assertEqual(len(state["continuations"]), 2)
        self.assertTrue(all(abs(wall - time.time()) < 5 for wall in state["continuations"]))
        with self.assertRaisesRegex(RuntimeError, "exactly one message"):
            send("please continue")   # Still refused after a continuation.

    def test_the_continuation_the_product_sends_is_marked_as_angelos_own(self):
        # The lead's continuation when its workers end passes host_request (worker_resume); a person's send never does.
        text = (ADDON / "worker_resume.py").read_text(encoding="utf-8")
        self.assertIn("host_request=parts", text)
        controller = (ADDON / "controller.py").read_text(encoding="utf-8")
        self.assertIn("def send(self, text, observation=None, *, recovery_evidence=None, host_request=None):", controller)


class ApprovalTests(unittest.TestCase):
    def test_scene_edits_are_approved_and_everything_else_declined(self):
        decide = oneshot_session.approval_decision
        for name in ("angelo_scene_program", "angelo_apply_batch", "angelo_create_cube", "angelo_set_transform",
                     "angelo_apply_recipe", "angelo_execute_python", "angelo_job_start", "angelo_sculpt_batch",
                     "angelo_prepare_sculpt", "angelo_accept_preview", "angelo_discard_preview"):
            with self.subTest(tool=name):
                self.assertEqual(decide(name), "approve")
        for name in ("angelo_write_file", "angelo_delete_file", "angelo_delegate", "angelo_memory_update",
                     "angelo_set_workspace_mode", "angelo_studio_generate", "angelo_studio_export",
                     "angelo_flow_build", "angelo_flow_run", "angelo_team_library_save", "angelo_unknown"):
            with self.subTest(tool=name):
                self.assertEqual(decide(name), "decline")
        self.assertEqual(decide("angelo_execute_python", "decline"), "decline")
        self.assertEqual(decide("angelo_scene_program", "decline"), "approve")
        self.assertEqual(session().decide("angelo_job_start"), "approve")


class CapTests(unittest.TestCase):
    def test_angelo_side_is_stopped_at_the_cost_cap(self):
        one, controller = session(cost_cap=10), Controller()
        one.controller = controller
        controller._request_metrics.usage = dict(ASTRA_USAGE, input_tokens=500_000, output_tokens=50_000)   # 7.5 USD.
        self.assertEqual(one.watch(), oneshot_session.WATCH_INTERVAL_S)
        self.assertEqual((controller.stops, one.state["stop"]), (0, None))
        self.assertAlmostEqual(one.state["watch"]["cost_usd"], 7.5)
        controller._request_metrics.usage = dict(ASTRA_USAGE)                                                # 15 USD.
        one.watch()
        self.assertEqual(controller.stops, 1)
        self.assertEqual(one.state["stop"], {"reason": "cost_cap", "cost_usd": 15.0, "cap_usd": 10.0})
        one.watch()
        self.assertEqual(controller.stops, 1)                     # Stop is pressed once.
        one.finished = True
        self.assertIsNone(one.watch())                            # The timer ends with the session.

    def test_codex_running_total_counts_too(self):
        one, controller = session(cost_cap=10), Controller()
        one.controller = controller
        controller.provider = NS(_usage=NS(total=dict(ASTRA_USAGE), updates=7))
        one.watch()
        self.assertEqual(controller.stops, 1)
        self.assertEqual(one.state["watch"]["requests"], 7)

    def test_default_cap_is_the_tests_own_and_an_unpriced_model_is_never_stopped_blindly(self):
        one = session()
        self.assertEqual(one.cost_cap, footbridge.LIMITS["cost_usd"])
        self.assertEqual(one.report["oneshot"]["limits"], footbridge.LIMITS)
        unknown, controller = session(cost_cap=1, model="some-new-model"), Controller()
        unknown.controller = controller
        controller._request_metrics.usage = dict(ASTRA_USAGE)
        unknown.watch()
        self.assertEqual(controller.stops, 0)
        self.assertIsNone(unknown.state["watch"]["cost_usd"])     # Recorded as unknown, not as zero.

    def claude_line(self, identity, output_tokens):
        return (0.0, {"type": "assistant", "message": {"id": identity, "usage": {
            "input_tokens": 500_000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "output_tokens": output_tokens}}})

    def test_mcp_side_is_interrupted_at_the_cost_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            slices = [([self.claude_line("m1", 10_000)], None, "deadline"),      # 5.5 USD: under the cap.
                      ([self.claude_line("m2", 60_000)], None, "deadline"),      # 13.5 USD in total: over it.
                      ([(0.0, {"type": "result", "subtype": "error_during_execution"})],
                       {"type": "result", "subtype": "error_during_execution"}, None)]
            stream, calls = OneMessageTests.stream(self, slices)
            output = {}
            config = OneMessageTests.config(self, folder, cost_cap=10)
            mcp_scenario.guard(stream, config, Path(folder), output)
            events, result, problem = stream.collect(time.monotonic() + 600, "brief")
        self.assertEqual(calls["interrupts"], 1)
        self.assertEqual(output["oneshot"]["stop"], {"reason": "cost_cap", "cost_usd": 13.5, "cap_usd": 10.0})
        self.assertEqual((len(events), result["subtype"], problem), (3, "error_during_execution", None))
        record = {"status": "failed", "result": {"subtype": "error_during_execution"}, "tool_calls": []}
        self.assertEqual(metrics.mcp_path(record, [], output["oneshot"])["stop"]["reason"], "cost_cap")

    def test_mcp_time_cap_is_the_drivers_own_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            stream, calls = OneMessageTests.stream(self)
            output = {}
            mcp_scenario.guard(stream, OneMessageTests.config(self, folder), Path(folder), output)
            with mock.patch.object(oneshot_mcp, "SLICE_S", 0.01):
                events, result, problem = stream.collect(time.monotonic() + 0.05, "brief")
        self.assertEqual((events, result, problem), ([], None, "deadline"))
        self.assertEqual((calls["interrupts"], output["oneshot"]["stop"]), (0, None))   # The driver interrupts, not the guard.
        self.assertEqual(metrics.mcp_path({"status": "timeout", "tool_calls": []}, [], output["oneshot"])["stop"]["reason"],
                         "time_cap")
        self.assertEqual(metrics.angelo_path({"status": "timeout", "oneshot": {"rows": []}})["stop"]["reason"], "time_cap")
        self.assertEqual(oneshot_mcp.cost_cap(OneMessageTests.config(self, "/x")), footbridge.LIMITS["cost_usd"])


class StillTests(unittest.TestCase):
    WANTED = [{"name": "footbridge-final.png", "min_size": [2560, 1440], "label": "the final still"}]

    def test_a_still_must_be_this_runs_and_large_enough(self):
        with tempfile.TemporaryDirectory() as folder:
            renders = Path(folder, "renders")
            renders.mkdir()
            self.assertFalse(oneshot_checks.evaluate_outputs(self.WANTED, [folder])[0]["passed"])
            still = renders / "footbridge-final.png"
            still.write_bytes(PNG)
            now = time.time()
            entry, = oneshot_checks.evaluate_outputs(self.WANTED, [folder], since=now - 60)
            self.assertTrue(entry["passed"])
            self.assertEqual((entry["path"], entry["details"]["size"]), (str(still), [2560, 1440]))
            os.utime(still, (now - 3600, now - 3600))             # A file an earlier run left behind.
            entry, = oneshot_checks.evaluate_outputs(self.WANTED, [folder], since=now - 60)
            self.assertFalse(entry["passed"])
            self.assertIn("written before this run", entry["details"]["files"][0]["problems"])
            still.write_bytes(PNG.replace((2560).to_bytes(4, "big"), (1280).to_bytes(4, "big")))
            entry, = oneshot_checks.evaluate_outputs(self.WANTED, [folder], since=now - 60)
            self.assertIn("size", entry["details"]["files"][0]["problems"])

    def test_a_plain_render_is_found_where_the_scenes_render_path_points(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            Path(elsewhere, "footbridge-final.png").write_bytes(PNG)
            self.assertFalse(oneshot_checks.evaluate_outputs(self.WANTED, [folder])[0]["passed"])
            for render_path in (str(Path(elsewhere, "footbridge-final.png")), str(Path(elsewhere, "footbridge-final")),
                                elsewhere + os.sep):
                with self.subTest(render_path=render_path):
                    self.assertTrue(oneshot_checks.evaluate_outputs(self.WANTED, [folder], None, render_path)[0]["passed"])
            Path(elsewhere, "footbridge-final.png").rename(Path(elsewhere, "other.png"))
            self.assertFalse(oneshot_checks.evaluate_outputs(self.WANTED, [folder], None, elsewhere + os.sep)[0]["passed"])

    @unittest.skipIf(os.name == "nt", "The one-shot eval harness runs on macOS: it reads POSIX paths from a reply")
    def test_a_path_the_side_stated_is_looked_at(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            still = Path(elsewhere, "footbridge-final.png")
            still.write_bytes(PNG)
            reply = "Done. The still is at %s (2560 x 1440)." % still
            self.assertEqual(oneshot_checks.mentioned_paths(reply, "footbridge-final.png"), [str(still)])
            script = json.dumps({"code": "scene.render.filepath = '%s'\nbpy.ops.render.render(write_still=True)" % still})
            self.assertEqual(oneshot_checks.mentioned_paths(script, "footbridge-final.png"), [str(still)])
            self.assertFalse(oneshot_checks.evaluate_outputs(self.WANTED, [folder])[0]["passed"])
            entry, = oneshot_checks.evaluate_outputs(self.WANTED, [folder], time.time() - 60, "", reply)
            self.assertEqual((entry["passed"], entry["path"]), (True, str(still)))
            relative = "saved as footbridge-final.png in renders/"   # No absolute path: found by name instead.
            self.assertEqual(oneshot_checks.mentioned_paths(relative, "footbridge-final.png"), [])
            self.assertEqual(oneshot_checks.mentioned_paths("~/x/Footbridge-Final.PNG", "footbridge-final.png"),
                             [os.path.expanduser("~/x/Footbridge-Final.PNG")])
            stream = Path(folder, "claude-stream.jsonl")
            stream.write_text(json.dumps({"message": {"content": [{"type": "text", "text": reply}]}}) + "\n")
            self.assertIn(str(still), oneshot_mcp.said(folder, {"result": {"reply": "Done."}}))

    def test_outputs_count_with_the_closed_checks(self):
        scene = {"success": True, "passed": 2, "total": 2, "results": [{"index": 0, "passed": True},
                                                                       {"index": 1, "passed": True}]}
        with tempfile.TemporaryDirectory() as folder:
            result = oneshot_checks.with_outputs(scene, self.WANTED, [folder])
            self.assertEqual((result["success"], result["passed"], result["total"]), (False, 2, 3))
            Path(folder, "footbridge-final.png").write_bytes(PNG)
            result = oneshot_checks.with_outputs(scene, self.WANTED, [folder])
            self.assertEqual((result["success"], result["passed"], result["results"][-1]["index"]), (True, 3, 2))
        self.assertEqual(oneshot_checks.with_outputs(scene, None, [])["total"], 2)

    def test_mcp_side_grades_and_keeps_the_still(self):
        task = specs.tasks("oneshot_footbridge")[0]
        with tempfile.TemporaryDirectory() as folder:
            trial, working = Path(folder, "trial"), Path(folder, "blender-cwd")
            trial.mkdir()
            (working / "renders").mkdir(parents=True)
            (working / "renders" / "footbridge-final.png").write_bytes(PNG)
            client = NS(execute=lambda code: {"path": "/tmp/"})
            record = {"host_check": {"success": True, "passed": 1, "total": 1, "results": [{"index": 0, "passed": True}]}}
            output = {"oneshot": {"folder": str(trial), "sent_wall": time.time() - 30}}
            config = {"task_set": "oneshot-footbridge", "claude_cwd": str(Path(folder, "cwd"))}
            oneshot_mcp.grade_outputs(client, task, config, record, output, [str(working)])
            self.assertTrue(record["host_check_passed"])
            self.assertEqual(record["host_check"]["total"], 2)
            self.assertEqual(Path(output["oneshot"]["still"]), trial / "agent-final.png")
            self.assertEqual((trial / "agent-final.png").read_bytes(), PNG)

    def test_angelo_side_keeps_a_copy_of_the_still(self):
        with tempfile.TemporaryDirectory() as folder:
            one = scenario_session.for_trial("oneshot-footbridge", Path(folder))
            source = Path(folder, "workspace-footbridge-final.PNG")
            source.write_bytes(PNG)
            result = {"results": [{"kind": "object", "passed": True},
                                  {"kind": "output", "passed": True, "path": str(source)}]}
            self.assertEqual(Path(one.keep_still(result)), Path(folder, "agent-final.png"))
            self.assertIsNone(one.keep_still({"results": [{"kind": "output", "passed": False}]}))
            self.assertEqual(one.output_roots("/ws")[0], "/ws")


if __name__ == "__main__":
    unittest.main()
