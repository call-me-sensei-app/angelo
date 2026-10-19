# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The mini one-shot tests: their check kinds on stand-ins, the group command, their metrics and dry run.

The kinds run inside Blender; these tests pin their logic on the host. The
compiled-app proof is each mini's reference job (its checks fail on the
factory scene and pass on the ground truth) in stock Blender.
"""

import contextlib
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_vs_angelo as bench  # noqa: E402
import oneshot_fake_models as oneshot_fake  # noqa: E402
import oneshot_group as group  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import oneshot_mini_checks as checks  # noqa: E402
import oneshot_minis  # noqa: E402
import oneshot_report as report  # noqa: E402
import oneshot_tables as tables  # noqa: E402
import scenario_fake_models as fake  # noqa: E402
import scenario_specs as specs  # noqa: E402
from test_native_oneshot_metrics import angelo_record, claude_lines, mcp_record, ROWS  # noqa: E402
from test_native_scenario_checks import FakeVector, Offset, mathutils  # noqa: E402

MINIS = specs.ONESHOT_GROUPS["oneshot-minis"]


def mesh_object(name, vertices=8, dimensions=(2.0, 2.0, 2.0), location=(0.0, 0.0, 0.0), modifiers=(), kind="MESH"):
    return NS(name=name, type=kind, data=NS(vertices=[NS(co=FakeVector((1, 1, 1)))] * vertices),
              modifiers=list(modifiers), dimensions=dimensions, location=location)


def box(low, high):
    return (list(low), list(high))


# A flat footbridge as connected pieces: the deck, four posts, two rails (world bounds).
BRIDGE = [box((-2, -0.6, 0), (2, 0.6, 0.15))] + [
    box((x - 0.06, y - 0.06, 0.15), (x + 0.06, y + 0.06, 1.15)) for x in (-1.88, 1.88) for y in (-0.52, 0.52)] + [
    box((-2, y - 0.04, 1.02), (2, y + 0.04, 1.10)) for y in (-0.52, 0.52)]


class BuiltTests(unittest.TestCase):
    def test_only_the_untouched_factory_cube_is_looked_past(self):
        self.assertTrue(checks.untouched_cube(mesh_object("Cube")))
        for changed in (mesh_object("Cube", dimensions=(4.0, 1.2, 0.15)), mesh_object("Cube", location=(0, 0, 1)),
                        mesh_object("Cube", vertices=96), mesh_object("Cube", modifiers=[NS(type="BEVEL")]),
                        mesh_object("Cube.001"), mesh_object("Deck")):
            self.assertFalse(checks.untouched_cube(changed))
        scene = NS(objects=[mesh_object("Cube"), mesh_object("Deck"), NS(name="Light", type="LIGHT"),
                            NS(name="Camera", type="CAMERA"), mesh_object("Rail", kind="CURVE")])
        self.assertEqual([obj.name for obj in checks.built_objects(scene)], ["Deck", "Rail"])
        reshaped = NS(objects=[mesh_object("Cube", dimensions=(4.0, 1.2, 0.15), location=(0, 0, 0.075))])
        self.assertEqual([obj.name for obj in checks.built_objects(reshaped)], ["Cube"])   # Now part of the build.

    def test_plan_is_the_extent_of_what_was_built(self):
        rule = {"long": [3.7, 4.6], "short": [1.1, 1.8], "centre": [0.0, 0.0], "radius": 0.4}
        scene = NS(objects=[])
        with mock.patch.object(checks, "components", return_value=BRIDGE):
            passed, details = checks._check_plan(None, scene, None, rule)
        self.assertTrue(passed, details)
        self.assertEqual((details["long"], details["short"]), (4.0, 1.2))
        along_y = [box((low[1], low[0], low[2]), (high[1], high[0], high[2])) for low, high in BRIDGE]
        with mock.patch.object(checks, "components", return_value=along_y):
            self.assertTrue(checks._check_plan(None, scene, None, rule)[0])                # Either axis.
        moved = [box((low[0] + 3, low[1], low[2]), (high[0] + 3, high[1], high[2])) for low, high in BRIDGE]
        with mock.patch.object(checks, "components", return_value=moved):
            self.assertEqual(checks._check_plan(None, scene, None, rule)[1]["problems"], ["centre"])
        with mock.patch.object(checks, "components", return_value=BRIDGE[:1] + [box((-4, -0.6, 0), (4, 0.6, 0.1))]):
            self.assertEqual(checks._check_plan(None, scene, None, rule)[1]["problems"], ["long"])
        with mock.patch.object(checks, "components", return_value=[]):
            self.assertEqual(checks._check_plan(None, scene, None, rule), (False, {"reason": "nothing was built"}))

    def slab(self, height, rule):
        scene = NS(objects=[])
        with mock.patch.object(checks, "components", return_value=BRIDGE), \
                mock.patch.object(checks, "top_height", side_effect=lambda s, d, x, y, names: height(x, y)):
            return checks._check_slab(None, scene, None, rule)

    def test_slab_is_flat_or_arched_as_asked(self):
        flat = {"size": [4.0, 1.2], "centre": [0.0, 0.0], "cover": 0.85, "rise": [-0.05, 0.05]}
        arched = dict(flat, rise=[0.08, 0.8])
        level = lambda x, y: 0.15
        arch = lambda x, y: 0.15 + 0.3 * (1.0 - (x / 2.0) ** 2)
        passed, details = self.slab(level, flat)
        self.assertTrue(passed, details)
        self.assertEqual((details["axis"], details["cover"], details["rise"]), ("x", 1.0, 0.0))
        self.assertEqual(self.slab(level, arched)[1]["problems"], ["rise"])
        passed, details = self.slab(arch, arched)
        self.assertTrue(passed, details)
        self.assertAlmostEqual(details["rise"], 0.3 * (1.88 / 2.0) ** 2, places=3)
        self.assertEqual(self.slab(arch, flat)[1]["problems"], ["rise"])
        short = lambda x, y: 0.15 if abs(x) < 1.0 else None                 # A 2 m deck: half the rays find nothing.
        self.assertEqual(self.slab(short, flat)[1]["problems"], ["cover", "rise"])

    def members(self, pieces, **rule):
        with mock.patch.object(checks, "components", return_value=pieces):
            return checks._check_members(None, NS(objects=[]), None, rule)

    def test_posts_and_rails_are_counted_by_shape(self):
        self.assertEqual(self.members(BRIDGE, shape="post", count=[4, 4]), (True, mock.ANY))
        self.assertEqual(self.members(BRIDGE, shape="rail", count=[2, 2])[1]["rails"], 2)
        self.assertFalse(self.members(BRIDGE[:4] + BRIDGE[5:], shape="post", count=[4, 4])[0])      # Three posts.
        round_post = box((0, 0, 0), (0.3, 0.12, 1.0))                                                 # Not square.
        wall = box((0, 0, 0), (0.6, 0.6, 1.0))                                                        # Not slender.
        self.assertEqual(self.members(BRIDGE + [round_post, wall], shape="post", count=[4, 4])[1]["posts"], 4)
        arched_rail = box((-2, 0.48, 1.0), (2, 0.56, 1.4))
        self.assertEqual(self.members([arched_rail], shape="rail", count=[1, 1])[0], False)          # Too tall when flat,
        self.assertTrue(self.members([arched_rail], shape="rail", count=[1, 1], max_height=1.0)[0])   # fine when arched.
        self.assertEqual(self.members(BRIDGE[:1], shape="rail", count=[0, 0])[0], True)              # The deck is no rail.


class CubeAndLightTests(unittest.TestCase):
    def bevelled(self, width=0.02, levels=(0.0, 0.01, 0.0173, 0.02)):
        """Vertices of a box bevelled on all edges: flat faces, the profile where strips end, a corner patch."""
        inner = 1.0 - width
        points = [(1.0, 0.5, 0.5), (0.5, 1.0, 0.5), (0.5, 0.5, 1.0)]
        points += [(1.0 - step, inner + step if step < width else 1.0, inner) for step in levels]
        points += [(0.9896, 0.9947, 0.9947)]                               # Inside a corner patch: not a profile point.
        return NS(vertices=[NS(co=FakeVector(point)) for point in points])

    def test_bevel_is_read_from_geometry(self):
        self.assertEqual(checks.bevel_of_mesh(self.bevelled()), (0.02, 3))
        self.assertEqual(checks.bevel_of_mesh(self.bevelled(levels=(0.0, 0.02))), (0.02, 1))
        plain = NS(vertices=[NS(co=FakeVector((x, y, z))) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
        self.assertEqual(checks.bevel_of_mesh(plain), (0.0, 0))
        self.assertEqual(checks.bevel_of_mesh(NS(vertices=[])), (0.0, 0))

    def cube(self, modifiers=(), mesh=None):
        mesh = mesh or NS(vertices=[NS(co=FakeVector((x, y, z))) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
        return NS(objects=[NS(name="Cube", type="MESH", data=mesh, modifiers=list(modifiers))])

    def test_bevel_as_a_modifier_or_as_geometry(self):
        rule = {"target": {"name": "Cube"}, "width": 0.02, "segments": 3}
        modifier = NS(type="BEVEL", show_viewport=True, width=0.02, segments=3, name="Bevel")
        passed, details = checks._check_bevel(None, self.cube([modifier]), None, rule)
        self.assertEqual((passed, details["route"]), (True, "modifier"))
        passed, details = checks._check_bevel(None, self.cube(mesh=self.bevelled()), None, rule)
        self.assertEqual((passed, details["route"]), (True, "geometry"))
        for wrong in (NS(type="BEVEL", show_viewport=True, width=0.2, segments=3, name="B"),
                      NS(type="BEVEL", show_viewport=True, width=0.02, segments=2, name="B"),
                      NS(type="BEVEL", show_viewport=False, width=0.02, segments=3, name="B")):
            self.assertFalse(checks._check_bevel(None, self.cube([wrong]), None, rule)[0])
        self.assertEqual(checks._check_bevel(None, self.cube(), None, rule),
                         (False, {"bevels": "no bevel on a matching object"}))

    def test_modifier_settings(self):
        rule = {"target": {"name": "Cube"}, "type": "SUBSURF", "values": {"levels": 2}}
        level = lambda value, shown=True: NS(type="SUBSURF", show_viewport=shown, levels=value, name="Subdivision")
        self.assertTrue(checks._check_modifier_settings(None, self.cube([level(2)]), None, rule)[0])
        self.assertFalse(checks._check_modifier_settings(None, self.cube([level(1)]), None, rule)[0])
        self.assertFalse(checks._check_modifier_settings(None, self.cube([level(2, False)]), None, rule)[0])
        self.assertFalse(checks._check_modifier_settings(None, self.cube(), None, rule)[0])

    def light(self, name, location, energy, kind="AREA", aimed=True):
        length = math.sqrt(sum(v * v for v in location))
        towards_light = [v / length for v in location] if aimed else [0.0, 0.0, 1.0]   # A light shines along its -Z.
        return NS(name=name, type="LIGHT", data=NS(type=kind, energy=energy),
                  matrix_world=Offset(location, z_axis=towards_light))

    def rig(self, *lights, camera=oneshot_minis.FACTORY_CAMERA):
        scene = NS(objects=list(lights), camera=NS(matrix_world=Offset(camera)) if camera else None)
        rule = {"type": "AREA", "count": [3, 3], "aim": [0.0, 0.0, 0.0], "min_dot": 0.9, "behind": [1, 1],
                "key_over_fill": 1.2}
        with mathutils():
            return checks._check_light_rig(None, scene, None, rule)

    def test_three_point_rig_is_read_from_the_lights(self):
        key, fill = self.light("Key", (4.0, -4.0, 5.0), 1000.0), self.light("Fill", (-2.0, -6.0, 2.5), 250.0)
        rim = self.light("Rim", (-5.0, 5.0, 3.5), 600.0)
        point = self.light("Light", (4.0, 1.0, 5.9), 1000.0, kind="POINT", aimed=False)    # The factory light: ignored.
        passed, details = self.rig(key, fill, rim, point)
        self.assertTrue(passed, details)
        self.assertEqual([light["behind"] for light in details["lights"]], [False, False, True])
        self.assertEqual(self.rig(key, fill, point)[1]["problems"], ["count", "behind the subject"])
        front_rim = self.light("Rim", (3.0, -5.0, 4.0), 600.0)
        self.assertEqual(self.rig(key, fill, front_rim)[1]["problems"], ["behind the subject"])
        side_fill = self.light("Fill", (-4.0, -2.0, 2.0), 250.0)                 # Far out to the side is still a fill.
        self.assertTrue(self.rig(key, side_fill, rim)[0])
        equal = self.light("Fill", (-2.0, -6.0, 2.5), 1000.0 * 46.25 / 57.0)
        self.assertEqual(self.rig(key, equal, rim)[1]["problems"], ["key no stronger than fill"])
        astray = self.light("Fill", (-2.0, -6.0, 2.5), 250.0, aimed=False)
        self.assertEqual(self.rig(key, astray, rim)[1]["problems"], ["aim"])
        self.assertIn("behind the subject", self.rig(key, fill, rim, camera=None)[1]["problems"])


class GroupTests(unittest.TestCase):
    ARGV = ["--authorize-billed-runs", "--task-set", "oneshot-minis", "--trials", "1", "--sides", "angelo",
            "--output", "/runs/minis"]

    def test_a_group_is_named_by_the_task_set(self):
        self.assertEqual(group.group_of(self.ARGV), "oneshot-minis")
        self.assertEqual(group.group_of(["--task-set=oneshot-minis"]), "oneshot-minis")
        for argv in ([], ["--task-set", "oneshot-mini-cube"], ["--task-set", "default"], ["--task-set"]):
            self.assertIsNone(group.group_of(argv))

    def test_each_member_gets_the_same_command_and_its_own_folder(self):
        argv = group.member_argv(self.ARGV, "oneshot-mini-cube")
        self.assertEqual(argv, ["--authorize-billed-runs", "--task-set", "oneshot-mini-cube", "--trials", "1",
                                "--sides", "angelo", "--output", str(Path("/runs/minis/oneshot-mini-cube"))])
        joined = group.member_argv(["--task-set=oneshot-minis", "--summarize-only=/runs/minis"], "oneshot-mini-camera")
        self.assertEqual(joined, ["--task-set", "oneshot-mini-camera", "--summarize-only",
                                  str(Path("/runs/minis/oneshot-mini-camera"))])
        self.assertEqual(bench.parse(argv).task_set, "oneshot-mini-cube")           # The bench accepts every member.
        self.assertEqual(bench.parse(argv).task_timeout, oneshot_minis.LIMITS["wall_s"])

    def run_group(self, argv, codes):
        calls = []

        def main(member):
            calls.append(member)
            return codes.get(member[member.index("--task-set") + 1], 0)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = group.run(argv, main)
        return code, [call[call.index("--task-set") + 1] for call in calls], printed.getvalue()

    def test_every_member_runs_in_order_as_its_own_run(self):
        code, ran, _ = self.run_group(self.ARGV, {})
        self.assertEqual((code, ran), (0, list(MINIS)))

    def test_a_billed_group_stops_at_the_first_member_that_does_not_end_cleanly(self):
        code, ran, printed = self.run_group(self.ARGV, {"oneshot-mini-footbridge-arched": 75})
        self.assertEqual((code, ran), (75, list(MINIS[:2])))                         # No message for the other three.
        self.assertIn("ONESHOT_GROUP_STOPPED", printed)
        dry = [item for item in self.ARGV if item != "--authorize-billed-runs"] + ["--dry-run-scenario"]
        code, ran, _ = self.run_group(dry, {"oneshot-mini-footbridge-arched": 1})
        self.assertEqual((code, ran), (1, list(MINIS)))                              # Unbilled: every member reported.

    def test_plan_only_prints_each_members_plan_and_starts_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()) as printed, mock.patch("subprocess.run") as run:
            code = bench.main(["--task-set", "oneshot-minis", "--trials", "1", "--plan-only"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        plans = [json.loads(line.split("PLANNED ", 1)[1]) for line in printed.getvalue().splitlines() if "PLANNED" in line]
        self.assertEqual([plan["task_set"] for plan in plans], list(MINIS))
        self.assertTrue(all(plan["billed_prompts"] == {"mcp": 1, "angelo": 1} for plan in plans))
        self.assertTrue(all(plan["oneshot"]["limits"] == oneshot_minis.LIMITS for plan in plans))

    def scorecard(self, verdict="passed"):
        angelo = metrics.angelo_path(angelo_record(), "claude-opus-5-5")
        other = metrics.mcp_path(mcp_record(), claude_lines(), {"messages": 1}, "claude-opus-5-5")
        side = lambda label, path, result: {"label": label, "verdict": result, "path": path, "reasons": [
            "check failed: two rails"] if result != "passed" else [], "checks": {"passed": 5, "total": 5}}
        return {"sides": [side("Blender MCP (Claude Code) 1", other, "passed"), side("Angelo 1", angelo, verdict)]}

    def test_one_table_over_the_members(self):
        with tempfile.TemporaryDirectory() as base:
            for member, verdict in zip(MINIS[:2], ("passed", "failed")):
                Path(base, member).mkdir()
                Path(base, member, report.SCORECARD).write_text(json.dumps(self.scorecard(verdict)))
            found = group.summarize(base, "oneshot-minis", {MINIS[0]: 0, MINIS[1]: 1})
            text = Path(base, group.SUMMARY).read_text()
            saved = json.loads(Path(base, group.RESULTS).read_text())
        self.assertEqual((len(found), saved["members"], len(saved["rows"])), (4, list(MINIS), 4))
        self.assertIn("| Test | Side | Verdict | Checks passed / failed / inconclusive | How the turn ended "
                      "| Typed / script calls | Each mutation in order | Lookups before the first change "
                      "| Output tokens | Seconds | Cost USD |", text)
        self.assertIn("| oneshot-mini-footbridge | Angelo 1 | passed | 5 / 0 / 0 of 5 | finished | 1 / 1 | typed "
                      "angelo_scene_program (3 ops); script (geometry) | 0 | 30000 | 512.0 |", text)
        self.assertIn("| oneshot-mini-footbridge-arched | Angelo 1 | failed |", text)
        self.assertIn("Not run: `oneshot-mini-cube`, `oneshot-mini-camera`, `oneshot-mini-lighting`.", text)
        self.assertIn("- `oneshot-mini-footbridge-arched`, Angelo 1 (failed): check failed: two rails", text)


class MetricTests(unittest.TestCase):
    def test_lookups_before_the_first_change_and_each_mutation_in_order(self):
        rows = [ROWS[0],
                {"role": "activity", "tool": "angelo_agent_capabilities", "status": "completed", "arguments": {}},
                {"role": "activity", "tool": "angelo_scene_object", "status": "completed", "arguments": {}},
                {"role": "activity", "tool": "angelo_blender_api", "status": "completed", "arguments": {}}] + ROWS[1:]
        record = angelo_record(activity={"capability_lookups": 1, "lookup_rounds": 1, "plan_rejections": 2,
                                         "plans_refused_in_part": 1},
                               oneshot={"user_messages": 1, "rows": metrics.summarize_rows(rows), "stop": None})
        path = metrics.angelo_path(record, "claude-opus-5-5")
        self.assertEqual(path["lookups"], {"calls": 3, "by_tool": {"angelo_agent_capabilities": 1, "angelo_blender_api": 1,
                                                                   "angelo_scene_object": 1},
                                           "capability_lookups": 1, "api_lookups": 1, "lookup_rounds": 1,
                                           "plan_rejections": 2, "plans_refused_in_part": 1})
        self.assertEqual(path["mutations"]["sequence"], ["typed angelo_scene_program (3 ops)", "script (geometry)"])
        text = "\n".join(tables.table([("Angelo 1", path, {"verdict": "passed", "checks": "5 / 5"})]))
        for expected in ("| Lookups before the first change, by tool | 3: angelo_agent_capabilities 1, "
                         "angelo_blender_api 1, angelo_scene_object 1 |",
                         "| Capability lookups / lookup rounds / rejected plans | 1 / 1 / 2 |",
                         "| Each mutation in order | typed angelo_scene_program (3 ops); script (geometry) |",
                         "| Output tokens | 30000 |",
                         "| Declined by the harness's approval policy (not a product failure) | none |"):
            self.assertIn(expected, text)

    def test_a_view_is_not_a_lookup_and_the_other_side_has_its_own(self):
        path = metrics.angelo_path(angelo_record())
        self.assertEqual(path["lookups"]["calls"], 0)                 # The views in ROWS come after the first change.
        names = ["mcp__blender__get_objects_summary", "mcp__blender__search_api_docs",
                 "mcp__blender__get_screenshot_of_window_as_image", "mcp__blender__execute_blender_code",
                 "mcp__blender__get_objects_summary"]
        record = mcp_record(tool_names=names, tool_calls=[{"name": name, "returned_ms": 1.0, "is_error": False}
                                                          for name in names])
        other = metrics.mcp_path(record, claude_lines(), {"messages": 1})
        self.assertEqual(other["lookups"]["by_tool"], {"get_objects_summary": 1, "search_api_docs": 1})
        self.assertEqual((other["lookups"]["api_lookups"], other["lookups"]["capability_lookups"]), (1, None))
        self.assertEqual(other["mutations"]["sequence"][0], "script (geometry)")
        row = next(line for line in tables.table([("x", other, {"verdict": "passed", "checks": "5 / 5"})])
                   if line.startswith("| Capability lookups"))
        self.assertTrue(row.endswith("| – |"))

    def test_a_policy_decline_is_named_as_the_harnesss_rule(self):
        record = angelo_record(declined=["angelo_write_file", "angelo_write_file", "angelo_studio_generate"])
        path = metrics.angelo_path(record)
        self.assertEqual(path["approvals"]["declined_by_policy"], {"angelo_write_file": 2, "angelo_studio_generate": 1})
        text = "\n".join(tables.table([("Angelo 1", path, {"verdict": "passed", "checks": "5 / 5"})]))
        self.assertIn("| Declined by the harness's approval policy (not a product failure) | angelo_write_file 2, "
                      "angelo_studio_generate 1 |", text)
        self.assertIn("file writes and deletes", metrics.DECLINE_POLICY)
        side = {"side": "angelo", "trial": 1, "runtime": "claude", "label": "Angelo 1", "verdict": "failed",
                "reasons": ["stopped: approval_declined"], "checks": {"passed": 2, "total": 5, "failed": []},
                "pixels": [], "path": path, "allowances": None}
        result = {"test": "oneshot-mini-cube", "title": "Cube", "finished_at": "now", "dry_run": False, "model": "m",
                  "runtime": "claude", "effort": "low", "limits": dict(oneshot_minis.LIMITS), "final_still": None,
                  "prompt": "Bevel the cube.", "prompt_sha256": "abc", "references": [], "unknown": {}, "sides": [side]}
        text = report.markdown(result)
        self.assertIn("The harness's approval policy declined: angelo_studio_generate, angelo_write_file. That is the "
                      "harness's rule", text)
        self.assertIn("not a fault of the product", text)
        self.assertIn("one message per side with no reference image, then nothing", text)
        self.assertIn("Limits: 300 s for the turn, 3 USD at list prices.", text)       # No render job to wait for.
        self.assertIn("This test asks for no still, so it has no pixel check", text)
        self.assertIn("| Angelo 1 | FAILED | 2 / 3 / 0 of 5 | none asked | finished |", text)   # It carried on.
        self.assertIn("None: the message is text only.", text)


class DryRunTests(unittest.TestCase):
    def test_a_minis_scripted_models_only_make_the_change(self):
        for task_set in MINIS:
            name = specs.scenario_id(task_set)
            task, = specs.tasks(name)
            with self.subTest(test=name):
                code = oneshot_fake.turn_code(name, task)
                self.assertEqual(code, fake.turn_code(name, "brief"))
                self.assertNotIn("render.render", code)
                self.assertIn("import scenario_%s as _scenario" % name, code)
                script = oneshot_fake.codex_script(name, [task])
                self.assertEqual([step[0] for step in script], ["exec", "message"])
                self.assertEqual(oneshot_fake.angelo_follow_up(task), [])
        heads = [specs.tasks(specs.scenario_id(task_set))[0]["prompt"][:120] for task_set in MINIS]
        self.assertEqual(len(set(heads)), len(heads))            # The scripted models tell the two footbridges apart.
        self.assertFalse(any(a != b and a in b for a in heads for b in heads))

    def test_dry_run_checks_of_a_test_without_a_still(self):
        path = metrics.angelo_path(angelo_record(approved=["angelo_execute_python"]))
        other = metrics.mcp_path(mcp_record(), claude_lines(), {"messages": 1})
        result = {"final_still": None, "sides": [
            {"side": "mcp", "trial": 1, "runtime": "claude", "verdict": "passed", "path": other},
            {"side": "angelo", "trial": 1, "runtime": "codex", "verdict": "passed", "path": path}]}
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, report.SCORECARD).write_text(json.dumps(result))
            found = report.dry_run_checks(folder)
        self.assertEqual(sorted(found), ["angelo-01: approvals counted and timed",
                                         "angelo-01: exactly one message was typed", "angelo-01: passed (closed checks)",
                                         "angelo-01: path metrics recorded", "mcp-01: exactly one message was typed",
                                         "mcp-01: passed (closed checks)", "mcp-01: path metrics recorded"])
        self.assertTrue(all(found.values()))


if __name__ == "__main__":
    unittest.main()
