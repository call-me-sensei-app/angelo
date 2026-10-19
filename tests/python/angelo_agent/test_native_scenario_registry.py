# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scenario registry, the bridge and island specs, reference files and the harness wiring (no Blender, process or account).

Also pins that the billed gates' own task sets are untouched: the default and
long sets carry no scenario fields, their MCP messages and Angelo launch
command are what they were, and the default approval rule still declines
reviewed Python.
"""

import ast
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_grading as grading  # noqa: E402
import bench_mcp_scenario as mcp_scenario  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import scenario_bridge as bridge  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_fake_models as fake  # noqa: E402
import scenario_island as island  # noqa: E402
import scenario_session  # noqa: E402
import scenario_specs as specs  # noqa: E402

CONNECTION = "11111111-1111-4111-8111-111111111111"
# Claude Code 2.1 re-encodes an attached image larger than this many bytes as a JPEG.
CLAUDE_CODE_IMAGE_BUDGET = 512_000


@contextlib.contextmanager
def reference_root(manifest_for=None):
    """A temporary references root (``ANGELO_SCENARIO_REFERENCES``), optionally with a bridge manifest."""
    with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {specs.REFERENCES_ENV: folder}):
        if manifest_for is not None:
            target = specs.references_dir("bridge")
            target.mkdir(parents=True)
            images = {}
            for view in manifest_for:
                data = ("jpeg bytes of %s" % view).encode()
                (target / (view + ".jpg")).write_bytes(data)
                images[view] = {"file": view + ".jpg", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            (target / specs.MANIFEST).write_text(json.dumps({"images": images}))
        yield Path(folder)


class RegistryTests(unittest.TestCase):
    def test_task_set_names(self):
        self.assertEqual(specs.TASK_SETS, ("scenario-bridge", "scenario-island", "scenario-heavy"))
        self.assertEqual(specs.scenario_id("scenario-island"), "island")
        self.assertEqual(specs.scenario_id("scenario-heavy"), "heavy")
        self.assertTrue(specs.is_scenario("scenario-bridge"))
        self.assertFalse(specs.is_scenario("default"))
        self.assertEqual(specs.scenario_id("scenario-bridge"), "bridge")
        for bad in ("default", "scenario-room", "scenario-"):
            with self.subTest(task_set=bad), self.assertRaises(ValueError):
                specs.scenario_id(bad)
        with self.assertRaises(ValueError):
            specs.module("room")

    def test_every_listed_scenario_is_complete(self):
        for name in specs.SCENARIO_IDS:
            module = specs.module(name)
            with self.subTest(scenario=name):
                for attribute in ("ID", "TITLE", "FRAME", "TURNS", "CAMERAS", "SUBJECT", "build", "build_turn"):
                    self.assertTrue(hasattr(module, attribute), attribute)
                self.assertEqual(module.ID, name)
                roles = {spec["role"] for spec in module.CAMERAS.values()}
                self.assertEqual(roles, {"reference", "closeup"})
                self.assertTrue({"front", "side", "top", "hero"} <= set(module.CAMERAS))

    def test_bridge_turns_are_well_formed(self):
        tasks = specs.tasks("bridge")
        self.assertEqual([task["id"] for task in tasks], ["valley", "bridge", "railings", "lamps", "water_rocks",
                                                          "golden_hour", "deck_camera", "car_animation",
                                                          "render_setup"])
        for task in tasks:
            with self.subTest(turn=task["id"]):
                self.assertEqual((task["kind"], task["requires_object"], task["checks"]), ("edit", False, []))
                self.assertLessEqual(len(task["references"]), specs.MAX_IMAGES_PER_TURN)
                self.assertTrue(set(task["references"]) <= set(bridge.CAMERAS))
                self.assertTrue(task["scenario_checks"])
                for check in task["scenario_checks"]:
                    self.assertIn(check["kind"], checks.CHECKS)
                    self.assertTrue(check.get("label"), check)
                    if check["kind"] == "probe":
                        self.assertEqual((len(check["origin"]), len(check["direction"])), (3, 3))
                        self.assertIn(check["expect"], ("hit", "miss"))
        self.assertEqual(tasks[0]["references"], ["front", "side", "top", "hero"])
        self.assertEqual(sum(len(t["scenario_checks"]) for t in tasks), specs.plan("bridge")["checks"])

    def test_every_check_target_is_a_name_the_prompts_state(self):
        for scenario in (bridge, island):
            prompts = " ".join(turn["prompt"] for turn in scenario.TURNS)
            for check in (c for turn in scenario.TURNS for c in turn["checks"]):
                target = check.get("target")
                if target and check.get("exists", True):   # Deleted defaults ("the default Cube") are not quoted.
                    with self.subTest(scenario=scenario.ID, label=check["label"]):
                        self.assertIn("'%s" % (target.get("name") or target.get("prefix")), prompts)

    def test_bridge_tasks_carry_no_output_or_job_fields(self):
        """The optional fields the island uses never appear on a scenario that does not declare them."""
        for task in specs.tasks("bridge"):
            self.assertFalse({"scenario_outputs", "scenario_job"} & set(task), task["id"])
        self.assertEqual((specs.approvable("bridge"), specs.job_wait("bridge")), (frozenset(), 0.0))
        self.assertNotIn("outputs", specs.plan("bridge"))

    def test_island_turns_are_well_formed(self):
        tasks = specs.tasks("island")
        self.assertEqual([task["id"] for task in tasks], ["blockout", "rock_form", "rock_material", "vegetation",
                                                          "water", "atmosphere", "lighting", "final_frame"])
        for task in tasks:
            with self.subTest(turn=task["id"]):
                self.assertEqual((task["kind"], task["requires_object"], task["checks"]), ("edit", False, []))
                self.assertEqual(task["references"], [])   # Directed in words: no reference image is attached.
                self.assertTrue(task["scenario_checks"])
                for check in task["scenario_checks"]:
                    self.assertIn(check["kind"], checks.CHECKS)
                    self.assertTrue(check.get("label"), check)
        # The dry run's scripted models find a turn by the first 120 characters of its prompt.
        heads = [task["prompt"][:120] for task in tasks]
        self.assertEqual(len(set(heads)), len(heads))
        for head in heads:
            self.assertEqual(sum(head in task["prompt"] for task in tasks), 1)
        self.assertEqual(sum(len(t["scenario_checks"]) for t in tasks), specs.plan("island")["checks"])
        self.assertTrue(specs.all_checks("island")[-1]["label"].startswith("final_frame: "))

    def test_island_final_turn_names_its_render_and_job(self):
        final = specs.tasks("island")[-1]
        self.assertEqual(final["scenario_outputs"], [{"name": "island-final.png", "min_size": [3840, 2160],
                                                      "label": "the 4K render was written"}])
        job = final["scenario_job"]
        self.assertEqual((job["kind"], job["output"], job["resolution"]),
                         ("RENDER_STILL", "renders/island-final.png", [3840, 2160]))
        self.assertIn("renders/island-final.png", final["prompt"])
        self.assertIn("3840 x 2160", final["prompt"])
        self.assertTrue(all("scenario_outputs" not in task and "scenario_job" not in task
                            for task in specs.tasks("island")[:-1]))
        self.assertEqual(specs.approvable("island"), frozenset({"angelo_job_start"}))
        self.assertGreaterEqual(specs.job_wait("island"), 3600)
        plan = specs.plan("island")
        self.assertEqual((plan["outputs"], plan["job_wait_s"]), (1, specs.job_wait("island")))
        # The job is what Angelo's job tool accepts: only its documented fields.
        from angelo_agent import job_spec
        self.assertLessEqual(set(job) - {"kind", "minutes", "queue"}, job_spec.FIELDS[job["kind"]])

    def test_island_ground_truth_agrees_with_the_prompts_and_checks(self):
        vertices, faces = island.island_geometry()
        xs, ys, zs = ([v[axis] for v in vertices] for axis in range(3))
        self.assertTrue(38 <= max(xs) - min(xs) <= 52 and 26 <= max(ys) - min(ys) <= 36)
        self.assertEqual(min(zs), island.KEEL_Z)
        self.assertLessEqual(max(zs), island.HIGH_GROUND)
        # A closed surface: every edge is shared by exactly two faces.
        edges = {}
        for face in faces:
            for a, b in zip(face, face[1:] + face[:1]):
                edges[frozenset((a, b))] = edges.get(frozenset((a, b)), 0) + 1
        self.assertEqual(set(edges.values()), {2})
        self.assertLess(island.top_height(*island.POND_CENTRE), island.top_height(0.0, 12.0))   # The basin.
        self.assertTrue(all(7.0 <= height <= 11.0 for _, height in island.PINES))
        camera = next(c for c in island.TURNS[0]["checks"] if c["kind"] == "camera")
        self.assertEqual((camera["location"], camera["look_at"]), (list(island.HERO["location"]),
                                                                   list(island.HERO["target"])))
        self.assertIn("(-11.5, -128, 18)", island.TURNS[0]["prompt"])
        self.assertIn("(-9, 0, 2.5)", island.TURNS[0]["prompt"])
        self.assertTrue(1 <= island.SUN_ELEVATION <= 9 and 90 < island.SUN_AZIMUTH < 180)   # Frame left, behind.
        for turn in (island.TURNS[0], island.TURNS[6]):   # The low sun is checked when set and after the look pass.
            low, high = next(c for c in turn["checks"] if c["kind"] == "light")["elevation_deg"]
            self.assertTrue(low <= island.SUN_ELEVATION <= high)
        self.assertEqual(island.CAMERAS["hero"]["location"], island.HERO["location"])
        focus = next(c for c in island.TURNS[-1]["checks"] if c["kind"] == "camera")["focus_distance"]
        near_rim = math.dist(island.HERO["location"], (0.0, -island.ISLAND_SIZE[1] / 2, 0.0))
        self.assertTrue(focus[0] <= near_rim <= focus[1])
        # The final size is set in the look pass, so that turn's checks fail before it and pass after it.
        final_size = next(c for c in island.TURNS[6]["checks"] if "render.resolution_x" in c.get("values", {}))
        self.assertEqual((final_size["values"]["render.resolution_x"], final_size["values"]["render.resolution_y"]),
                         island.FINAL_RENDER["size"])
        self.assertLess(island.NOTCH[0], -island.ISLAND_SIZE[0] / 2 + 3)   # The fall leaves the left-hand tip.
        self.assertEqual(set(island.BUILDERS), {turn["id"] for turn in island.TURNS})

    def test_all_checks_are_labelled_by_turn(self):
        labels = [check["label"] for check in specs.all_checks("bridge")]
        self.assertEqual(len(labels), specs.plan("bridge")["checks"])
        self.assertTrue(labels[0].startswith("valley: "))
        self.assertTrue(labels[-1].startswith("render_setup: "))

    def test_ground_truth_constants_agree_with_the_checks(self):
        self.assertEqual(bridge.terrain_height(0.0, 0.0), bridge.BED_Z)
        self.assertAlmostEqual(bridge.terrain_height(-9.5, 3.0), 0.0, places=6)
        self.assertLess(bridge.terrain_height(7.0, 0.0), 0.0)
        self.assertGreater(bridge.terrain_height(-52.0, -20.0), 1.0)
        self.assertAlmostEqual(bridge.arch_extrados(0.0), bridge.ARCH_SPRING_Z + bridge.ARCH_RISE_OUT)
        self.assertIsNone(bridge.arch_extrados(8.0))
        self.assertAlmostEqual(bridge.ARCH_SPRING_Z + bridge.ARCH_RISE_OUT, bridge.DECK_TOP - bridge.DECK_THICKNESS)
        self.assertEqual(len(bridge.ROCKS), 16)
        self.assertEqual(sum(x < 0 for x, _, _ in bridge.ROCKS), 8)
        self.assertTrue(all(4.5 <= abs(x) <= 8.0 and -18 <= y <= 18 for x, y, _ in bridge.ROCKS))
        # The sun the light check and the sky agree on: low and from the +X/-Y side.
        self.assertTrue(4 <= bridge.SUN_ELEVATION <= 18 and -90 < bridge.SUN_AZIMUTH < 0)

    def test_digest_covers_the_ground_truth_sources_only(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("scenario_bridge.py",) + specs.SHARED_SOURCES:
                shutil.copy(HERE / name, folder)
            with mock.patch.object(specs, "HERE", Path(folder)):
                first = specs.digest("bridge")
                self.assertEqual(len(first), 16)
                text = Path(folder, "scenario_capture.py").read_text()
                Path(folder, "scenario_capture.py").write_text(text.replace("\n", "\r\n"), newline="")
                self.assertEqual(specs.digest("bridge"), first)   # Line endings never change the references.
                Path(folder, "scenario_job.py").write_text(Path(folder, "scenario_job.py").read_text() + "\n# edit\n")
                self.assertNotEqual(specs.digest("bridge"), first)

    def test_reference_files_are_hash_checked(self):
        with reference_root(["front", "hero"]):
            files = specs.reference_files("bridge", ["front", "hero"])
            self.assertEqual([view for view, _, _ in files], ["front", "hero"])
            self.assertEqual(files[0][2], hashlib.sha256(b"jpeg bytes of front").hexdigest())
            Path(files[1][1]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                specs.reference_files("bridge", ["hero"])
        with reference_root(), self.assertRaises(FileNotFoundError):
            specs.reference_files("bridge", ["front"])

    def test_reference_budget_keeps_claude_code_from_re_encoding(self):
        self.assertLess(specs.REFERENCE_MAX_BYTES, CLAUDE_CODE_IMAGE_BUDGET)
        self.assertEqual(specs.media_type("/x/front.jpg"), "image/jpeg")
        self.assertEqual(specs.media_type("/x/front.PNG"), "image/png")

    def test_plan_names_the_reference_version(self):
        plan = specs.plan("bridge")
        self.assertEqual(plan["reference_digest"], specs.digest("bridge"))
        self.assertEqual(plan["scored_views"], list(bridge.CAMERAS))
        self.assertEqual(plan["frame"], bridge.FRAME)


class HarnessWiringTests(unittest.TestCase):
    def test_live_task_sets_include_the_scenarios(self):
        self.assertIn("scenario-bridge", live.TASK_SETS)
        self.assertEqual(live.tasks_for("scenario-bridge"), specs.tasks("bridge"))
        self.assertIn("scenario-island", live.TASK_SETS)
        self.assertEqual(live.tasks_for("scenario-island"), specs.tasks("island"))

    def test_billed_gate_sets_carry_no_scenario_fields(self):
        for name, tasks in (("default", live.tasks_for("default")), ("long", live.tasks_for("long", 40))):
            with self.subTest(task_set=name):
                self.assertTrue(tasks)
                for task in tasks:
                    self.assertFalse({"scenario", "scenario_checks", "references", "scenario_outputs",
                                      "scenario_job"} & set(task), task["id"])
        self.assertEqual(live.approval_decision("angelo_execute_python"), "decline")
        self.assertEqual(live.approval_decision("angelo_job_start"), "decline")   # Only a scenario may allow a job.
        self.assertEqual((eval_live_agent.job_wait(eval_live_agent.parse([])), bench.job_wait(bench.parse([]))), (0, 0))

    def test_scenario_approval_rule(self):
        decide = scenario_session.approval_decision
        self.assertEqual(decide("angelo_execute_python"), "approve")
        self.assertEqual(decide("angelo_execute_python", "decline"), "decline")
        self.assertEqual(decide("angelo_scene_program"), "approve")
        for name in ("angelo_write_file", "angelo_delete_file", "angelo_delegate", "angelo_memory_update",
                     "angelo_set_workspace_mode", "angelo_accept_preview", "angelo_studio_generate",
                     "angelo_sculpt_batch"):
            with self.subTest(tool=name):
                self.assertEqual(decide(name), "decline")
        self.assertIsNone(scenario_session.for_trial("default", Path("/tmp")))

    def test_a_scenario_may_only_add_the_job_tool(self):
        decide = scenario_session.approval_decision
        self.assertEqual(decide("angelo_job_start"), "decline")                       # The bridge: no job.
        self.assertEqual(decide("angelo_job_start", extra=specs.approvable("island")), "approve")
        self.assertEqual(decide("angelo_job_start", "decline", specs.approvable("island")), "approve")
        self.assertEqual(decide("angelo_execute_python", "decline", specs.approvable("island")), "decline")
        for name in ("angelo_write_file", "angelo_delete_file", "angelo_studio_generate", "angelo_delegate"):
            with self.subTest(tool=name):   # A scenario cannot widen the rule beyond SCENARIO_EXTRAS.
                self.assertEqual(decide(name, extra={name}), "decline")
        session = scenario_session.for_trial("scenario-island", Path("/tmp"), stage_copies=True)
        self.assertEqual((session.decide("angelo_job_start"), session.decide("angelo_write_file")),
                         ("approve", "decline"))
        self.assertTrue(session.stage_copies and session.job_wait_s == specs.job_wait("island"))
        bridge_session = scenario_session.for_trial("scenario-bridge", Path("/tmp"))
        self.assertEqual(bridge_session.decide("angelo_job_start"), "decline")
        self.assertFalse(bridge_session.jobs_pending())   # No job wait: the job manager is never asked.

    def test_a_scenario_turn_is_graded_by_its_own_checks(self):
        """Scenario tasks have an empty ``checks`` list like an ungradable survey request; they are never ungraded."""
        route = scenario_session.grading_route
        for name in specs.SCENARIO_IDS:
            for task in specs.tasks(name):
                self.assertEqual(route(task, True), "scenario", task["id"])
        self.assertEqual(route({"kind": "edit", "checks": []}, False), "ungraded")          # A survey request.
        self.assertEqual(route({"kind": "edit", "checks": [{"kind": "x"}]}, False), "checks")
        self.assertEqual(route({"kind": "read_only", "checks": []}, False), "read_only")
        self.assertEqual(route({"kind": "read_only", "checks": []}, True), "read_only")
        for task in live.tasks_for("default") + live.tasks_for("long", 40):
            self.assertIn(route(task, False), ("checks", "read_only"), task["id"])       # The gates are unchanged.

    def test_mcp_messages_without_references_are_unchanged(self):
        text = "Create a cube named 'Eval Cube'."
        self.assertIs(mcp_scenario.claude_content(text, []), text)
        self.assertEqual(mcp_scenario.codex_input(text, []), [{"type": "text", "text": text}])
        record = {"task": "cube_creation"}
        self.assertEqual(mcp_scenario.attach({"id": "cube_creation"}, record), [])
        self.assertEqual(record, {"task": "cube_creation"})

    def test_mcp_messages_carry_the_reference_bytes(self):
        with reference_root(["front", "hero"]):
            task = specs.tasks("bridge")[0]
            task["references"] = ["front", "hero"]
            record = {}
            paths = mcp_scenario.attach(task, record)
            self.assertEqual([entry["view"] for entry in record["references"]], ["front", "hero"])
            blocks = mcp_scenario.claude_content(task["prompt"], paths)
            self.assertEqual(blocks[0], {"type": "text", "text": task["prompt"]})
            self.assertEqual([b["source"]["media_type"] for b in blocks[1:]], ["image/jpeg", "image/jpeg"])
            self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]), b"jpeg bytes of front")
            self.assertEqual(mcp_scenario.codex_input("x", paths)[1], {"type": "localImage", "path": paths[0]})

    def test_dry_run_models_play_a_turns_render_job(self):
        final = specs.tasks("island")[-1]
        plain = fake.turn_code("island", "final_frame")
        self.assertNotIn("render.render", plain)   # Angelo's scripted runtime starts the job tool instead.
        code = fake.turn_code("island", "final_frame", final["scenario_job"])
        ast.parse(code)
        self.assertIn("bpy.ops.render.render(write_still=True)", code)
        self.assertIn("os.path.join(os.getcwd(), 'renders/island-final.png')", code)
        self.assertIn("resolution_x, _scene.render.resolution_y = 3840, 2160", code)
        self.assertLess(code.index("build_turn"), code.index("render.render"))
        with self.assertRaises(ValueError):
            fake.render_code({"kind": "RENDER_ANIMATION", "output": "frames"})
        script = fake.codex_script("island", specs.tasks("island"))
        self.assertEqual(sum("render.render" in step[1] for step in script), 1)
        self.assertEqual(fake.turn_code("bridge", "valley", None), fake.turn_code("bridge", "valley"))

    def test_mcp_side_grades_a_turns_outputs_in_blenders_own_folders(self):
        final = specs.tasks("island")[-1]
        ast.parse(mcp_scenario.OUTPUT_ROOTS_CODE)
        with tempfile.TemporaryDirectory() as folder:
            renders = Path(folder, "renders")
            renders.mkdir()

            class Client:
                def execute(self, code):
                    if code == mcp_scenario.OUTPUT_ROOTS_CODE:
                        return {"roots": [folder, "", None]}
                    return {"host_check": {"success": True, "passed": 1, "total": 1, "results": [
                        {"index": 0, "kind": "camera", "label": "dof", "passed": True}]}, "host_check_passed": True,
                            "state_digest": "d1"}
            record, output = {}, {"scenario": {"digests": ["d0"]}}
            mcp_scenario.grade(Client(), final, {"task_set": "scenario-island"}, record, output)
            self.assertFalse(record["host_check_passed"])   # No render file yet.
            self.assertEqual((record["host_check"]["passed"], record["host_check"]["total"]), (1, 2))
            (renders / "island-final.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (3840).to_bytes(4, "big")
                                                       + (2160).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
            record = {}
            mcp_scenario.grade(Client(), final, {"task_set": "scenario-island"}, record, output)
            self.assertTrue(record["host_check_passed"])
            self.assertEqual(record["host_check"]["results"][-1]["details"]["size"], [3840, 2160])
            self.assertEqual(output["scenario"]["digests"], ["d0", "d1", "d1"])

    def test_mcp_scenario_code_compiles(self):
        for name in specs.SCENARIO_IDS:
            for task in specs.tasks(name):
                ast.parse(grading.scenario_grade_code(task["id"], specs.TASK_SET_PREFIX + name))
        digest = grading.scenario_digest_code("scenario-bridge")
        ast.parse(digest)
        # The scenario module is imported while the helpers' path is in place: the model never imports it.
        prelude = digest[:digest.index("finally:")]
        self.assertIn("scenario_specs.module(scenario_specs.scenario_id('scenario-bridge'))", prelude)
        code = grading.scenario_finish_code("scenario-bridge", ["a", "b"], {"materials": ["Dots Stroke"]},
                                            Path("/tmp/final.blend"), Path("/tmp/finish.json"))
        ast.parse(code)
        self.assertIn("os.replace(", code)   # The driver reads the result only once it is complete.
        self.assertIn("scenario_hygiene.finish(", code)

    def test_bench_guards(self):
        def parse(*argv):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bench.parse(list(argv))
        parse("--dry-run-scenario")
        parse("--task-set", "scenario-bridge", "--after-failure", "reset")
        plan = bench.plan(bench.parse(["--task-set", "scenario-bridge", "--connection-id", CONNECTION]))
        self.assertEqual((plan["scenario"]["scenario"], plan["python_approvals"]), ("bridge", "approve"))
        self.assertNotIn("scenario", bench.plan(bench.parse(["--connection-id", CONNECTION])))

    def angelo_command(self, argv):
        args = bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, *argv])
        args.source_connections_dir = "/nonexistent"
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            bench.run_angelo_trial(args, Path(folder), 1)
        return run.call_args.args[0]

    def test_angelo_launch_command(self):
        default = self.angelo_command([])
        self.assertNotIn("--python-approvals", default)   # The billed gates' command is unchanged.
        scenario = self.angelo_command(["--task-set", "scenario-bridge", "--python-approvals", "decline"])
        self.assertEqual(scenario[scenario.index("--python-approvals") + 1], "decline")
        self.assertFalse({"--stage-copies", "--render-device"} & set(scenario))   # Only when asked for.
        showcase = self.angelo_command(["--task-set", "scenario-island", "--stage-copies", "--render-device", "gpu",
                                        "--demo-record", "--sides", "angelo"])
        self.assertIn("--stage-copies", showcase)
        self.assertEqual(showcase[showcase.index("--render-device") + 1], "gpu")

    def test_a_job_scenario_gets_time_for_its_render(self):
        args = bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--task-set", "scenario-island"])
        args.source_connections_dir = "/nonexistent"
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            bench.run_angelo_trial(args, Path(folder), 1)
        turns = len(specs.tasks("island"))
        self.assertEqual(run.call_args.kwargs["timeout"], turns * (2 * 300 + 60) + 300 + specs.job_wait("island"))
        self.assertEqual(eval_live_agent.job_wait(eval_live_agent.parse(["--task-set", "scenario-island"])),
                         specs.job_wait("island"))
        self.assertEqual(bench.DRY_RUN_SIDES["angelo"], ("angelo",))
        self.assertEqual(bench.DRY_RUN_SIDES["both"], ("mcp-claude", "mcp-codex", "angelo"))

    def test_eval_live_agent_guards_and_environment(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                eval_live_agent.parse(["--task-set", "scenario-bridge", "--after-failure", "reset"])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            default = eval_live_agent.trial_environment(folder, folder, eval_live_agent.parse([]), 1)
            self.assertFalse({"ANGELO_EVAL_PYTHON_APPROVALS", specs.REFERENCES_ENV}
                             & set(name for name in default if name not in os.environ))
            args = eval_live_agent.parse(["--task-set", "scenario-bridge"])
            scenario = eval_live_agent.trial_environment(folder, folder, args, 1)
        self.assertEqual(scenario["ANGELO_EVAL_PYTHON_APPROVALS"], "approve")
        self.assertEqual(scenario[specs.REFERENCES_ENV], str(specs.reference_root()))
        self.assertEqual((scenario["ANGELO_EVAL_STAGE_COPIES"], scenario["ANGELO_EVAL_RENDER_DEVICE"]), ("", ""))
        with tempfile.TemporaryDirectory() as folder:
            args = eval_live_agent.parse(["--task-set", "scenario-island", "--stage-copies", "--render-device", "gpu"])
            showcase = eval_live_agent.trial_environment(Path(folder), Path(folder), args, 1)
            default = eval_live_agent.trial_environment(Path(folder), Path(folder), eval_live_agent.parse([]), 1)
        self.assertEqual((showcase["ANGELO_EVAL_STAGE_COPIES"], showcase["ANGELO_EVAL_RENDER_DEVICE"]), ("1", "gpu"))
        self.assertFalse({"ANGELO_EVAL_STAGE_COPIES", "ANGELO_EVAL_RENDER_DEVICE"}
                         & set(name for name in default if name not in os.environ))
        for flag in (["--stage-copies"], ["--render-device", "gpu"]):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                eval_live_agent.parse(flag)   # Scenario sets only: the gates' command line cannot carry them.


if __name__ == "__main__":
    unittest.main()
