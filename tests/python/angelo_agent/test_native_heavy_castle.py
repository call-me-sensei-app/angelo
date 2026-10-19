# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The heavy cases: the castle one-shot, the dense scene's five edits, their wiring and the native-reads report.

No Blender, process or account. The checks themselves are proven in stock
Blender by the reference jobs (fail before, pass after) and by
``oneshot_variants.py -- oneshot_castle`` and ``heavy_variants.py`` (right
and wrong builds); this pins what can be pinned without it.
"""

import ast
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_grading as grading  # noqa: E402
import bench_mcp_scenario as mcp_scenario  # noqa: E402
import bench_native_reads_paths as reads  # noqa: E402
import bench_native_reads_web as web  # noqa: E402
import castle_checks  # noqa: E402
import castle_kit  # noqa: E402
import final_runs_plan as campaign  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_heavy as heavy  # noqa: E402
import scenario_oneshot_castle as castle  # noqa: E402
import scenario_session  # noqa: E402
import scenario_specs as specs  # noqa: E402

try:
    import numpy  # noqa: F401
except ImportError:
    numpy = None
PINNED_STREAM = [0.8352227387494061, 0.09224760421828981, 0.777941377419688]
BRANDS = ("disney", "cinderella", "sleeping beauty", "magic kingdom", "theme park", "walt", "pixar", "efteling")


class CastleTests(unittest.TestCase):
    def test_registered_with_its_kit_in_the_digest(self):
        self.assertIn("oneshot-castle", specs.ONESHOT_TASK_SETS)
        self.assertEqual(specs.scenario_id("oneshot-castle"), "oneshot_castle")
        self.assertEqual(specs.ONESHOT_SOURCES["oneshot_castle"], ("castle_kit.py",))
        self.assertEqual(castle.TURNS[0]["references"], ["hero"])
        self.assertEqual(castle.GROUND_TRUTH, "reference")

    def test_no_brand_is_named_or_imitated(self):
        for text in (castle.PROMPT, castle.__doc__, castle_kit.__doc__, castle.TITLE):
            for brand in BRANDS:
                self.assertNotIn(brand, text.lower())
        self.assertIn("Neuschwanstein", castle.PROMPT)   # A real public building is the stated inspiration.

    def test_checks_follow_the_kit_and_the_brief(self):
        kinds = [check["kind"] for check in castle.CHECKS]
        for kind in ("round_tower", "crenellations", "encloses", "arch", "supported", "apart", "frames", "tallest"):
            self.assertIn(kind, kinds)
            self.assertIn(kind, checks.CHECKS)
        labels = [check["label"] for check in castle.CHECKS]
        self.assertEqual(len(labels), len(set(labels)))
        towers = [check for check in castle.CHECKS if check["kind"] == "round_tower"]
        self.assertEqual([tuple(check["centre"]) for check in towers], [t[0] for t in castle_kit.TOWERS.values()])
        self.assertIn("(-6.5, -17.5) and (6.5, -17.5)", castle.PROMPT)   # The gate towers' centres.
        camera = next(check for check in castle.CHECKS if check["kind"] == "camera")
        self.assertEqual((camera["location"], camera["look_at"]), (list(castle.HERO["location"]),
                                                                   list(castle.HERO["target"])))
        self.assertIn("(42, -74, 16)", castle.PROMPT)
        arch = next(check for check in castle.CHECKS if check["kind"] == "arch")
        crown = castle_kit.SPRINGING + castle_kit.ARCH_RADIUS
        self.assertTrue(arch["crown"][0] <= crown <= arch["crown"][1])
        self.assertEqual(arch["y"], -castle_kit.HALF_Y)

    def test_merlon_runs_count_what_stands_above_the_walk(self):
        walk, top = 9.0, 10.2
        heights = ([walk] * 8 + [top] * 10) * 5 + [walk] * 8
        self.assertEqual(castle_checks.merlon_runs(heights, 0.5)[0], 5)
        self.assertEqual(castle_checks.merlon_runs([walk] * 40, 0.5)[0], 0)
        self.assertEqual(castle_checks.merlon_runs([None, walk, top, None, top, walk], 0.5)[0], 2)
        self.assertEqual(castle_checks.merlon_runs([], 0.5), (0, None))

    def test_extent_covers_the_towers_and_keep(self):
        low, high = castle_kit.extent()
        self.assertEqual(high[2], castle_kit.KEEP_RIDGE)
        self.assertTrue(low[0] < -castle_kit.HALF_X and high[0] > castle_kit.HALF_X)


class HeavyScenarioTests(unittest.TestCase):
    def test_registered_as_a_multi_turn_scenario_with_a_start_file(self):
        self.assertIn("scenario-heavy", specs.TASK_SETS)
        self.assertEqual(specs.SCENARIO_SOURCES["heavy"],
                         ("heavy_scene.py", "heavy_plan.py", "heavy_scene_blend.py", "castle_kit.py"))
        tasks = specs.tasks("heavy")
        self.assertEqual([task["id"] for task in tasks], ["flag", "trees", "heaviest", "normals", "tower"])
        for task in tasks:
            self.assertEqual(task["references"], [])
            kinds = [check["kind"] for check in task["scenario_checks"]]
            self.assertEqual(kinds[-1], "untouched")
            self.assertEqual(len(kinds), 2)
            for check in task["scenario_checks"]:
                self.assertIn(check["kind"], checks.CHECKS)
        prompts = " ".join(turn["prompt"] for turn in heavy.TURNS)
        for name in ("'Flag'", "'Terrain'", "'Tree 001'", "'Castle Wall", "'Watchtower'"):
            self.assertIn(name, prompts)

    def test_start_file_comes_from_the_manifest(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {specs.REFERENCES_ENV: folder}):
            with self.assertRaises(FileNotFoundError):
                specs.start_file("heavy")
            target = specs.references_dir("heavy")
            target.mkdir(parents=True)
            (target / specs.MANIFEST).write_text(json.dumps({"start": {"file": "start.blend", "sha256": "ab" * 32}}))
            self.assertEqual(specs.start_file("heavy"), (target / "start.blend", "ab" * 32))
        self.assertIsNone(specs.start_file("bridge"))

    def test_the_mcp_side_appends_the_start_file_before_the_first_digest(self):
        client = mock.Mock()
        client.execute.side_effect = [{"setup_s": 2.0, "objects": 1969}, {"digest": "d", "orphans": {}}]
        output = {}
        with mock.patch.object(specs, "start_file", return_value=(Path("/refs/start.blend"), "ab" * 32)):
            mcp_scenario.begin(client, {"task_set": "scenario-heavy"}, output)
        first = client.execute.call_args_list[0]
        self.assertIn("/refs/start.blend", first.args[0])
        self.assertEqual(first.kwargs["timeout"], heavy.SETUP_TIMEOUT_S)
        self.assertEqual(output["scenario"]["setup"]["objects"], 1969)
        ast.parse(grading.scenario_setup_code("scenario-heavy", "/refs/start.blend", "ab" * 32))

    def test_the_angelo_side_appends_it_too(self):
        session = scenario_session.ScenarioSession("scenario-heavy", Path(tempfile.gettempdir()))
        window = SimpleNamespace(scene=object())
        with mock.patch.object(specs, "start_file", return_value=(Path("/refs/start.blend"), "ab")), \
                mock.patch.object(heavy, "setup", return_value={"objects": 1969}) as setup, \
                mock.patch.object(scenario_session.scenario_hygiene, "state_digest", return_value="d"), \
                mock.patch.object(scenario_session.scenario_hygiene, "orphans", return_value={}):
            report = session.begin(mock.Mock(), window)
        setup.assert_called_once()
        self.assertEqual(report["setup"]["objects"], 1969)
        self.assertEqual(report["digests"], ["d"])

    def test_the_campaign_runs_both_heavy_cases_on_every_model_and_side(self):
        steps = {step["id"]: step for step in campaign.campaign("windows")}
        for model in ("opus", "sol", "astra"):
            for task_set, prompts in (("oneshot-castle", 1), ("scenario-heavy", 5)):
                step = steps["h2h-%s-%s" % (model, task_set)]
                self.assertEqual((step["trials"], set(step["sides"]), step["prompts"]),
                                 (5, {"mcp", "angelo"}, {"mcp": prompts, "angelo": prompts}))
                self.assertIn("--angelo-always-allow-scripts", step["args"])   # "scripts allowed" (bench_integrity)


@unittest.skipIf(numpy is None, "the heavy scene's geometry needs NumPy")
class HeavyPlanTests(unittest.TestCase):
    def test_scale_and_the_facts_the_tasks_ask_about(self):
        import heavy_plan as plan
        import heavy_scene as scene
        import heavy_scene_blend as blend
        counts = plan.counts()
        self.assertEqual(counts["objects"], 1969)
        self.assertTrue(2_000_000 <= counts["vertices"] <= 5_000_000)
        faces = {name: len(plan.geometry(plan.entry(name))[1]) for name in (plan.STATUE, plan.RELIEF, plan.TERRAIN)}
        self.assertTrue(faces[plan.STATUE] > faces[plan.RELIEF] > faces[plan.TERRAIN] > 0.97 * faces[plan.STATUE])
        heaviest = max((item for item in plan.plan() if item.kind == "mesh"),
                       key=lambda item: len(plan.geometry(item)[1]))
        self.assertEqual(heaviest.name, plan.STATUE)
        positions = plan.geometry(plan.entry(plan.TERRAIN))[0]
        top = positions[int(positions[:, 2].argmax())]
        self.assertLess(math.hypot(top[0] - scene.SUMMIT[0], top[1] - scene.SUMMIT[1]), 10.0)
        for cx, cy, _, _ in scene.DECOYS:
            near = numpy.hypot(positions[:, 0] - cx, positions[:, 1] - cy) < 40.0
            self.assertGreater(top[2] - positions[near][:, 2].max(), 0.5)
        offsets = [item.location[2] - scene.surface(item.location[0], item.location[1])
                   for item in plan.plan() if item.name.startswith("Tree ")]
        self.assertEqual(sum(offset >= 0.5 for offset in offsets), scene.FLOATING)
        self.assertEqual(sum(offset <= -0.5 for offset in offsets), scene.SUNK)
        self.assertTrue(all(abs(offset) < 0.01 for offset in offsets if abs(offset) < 0.5))
        for side, (_, _, flipped) in scene.WALLS.items():
            consistent, volume = blend.wall_volume_ok(*plan.geometry(plan.entry("Castle Wall " + side))[:3])
            self.assertEqual((consistent, volume > 0), (not flipped, True), side)

    def test_the_seeded_stream_and_quantum_are_fixed(self):
        import heavy_scene as scene
        first, second = scene.Stream(scene.SEED), scene.Stream(scene.SEED)
        values = [first.uniform() for _ in range(3)]
        self.assertEqual(values, [second.uniform() for _ in range(3)])
        self.assertEqual(values, PINNED_STREAM)   # Placement never depends on NumPy's own generators.
        self.assertEqual(float(scene.q([0.12345678])[0]), float(numpy.float32(0.1235)))


class NativeReadsReportTests(unittest.TestCase):
    def test_every_path_has_both_routes_and_a_unit(self):
        ids = [path.id for path in reads.PATHS]
        self.assertEqual(len(ids), len(set(ids)))
        for path in reads.PATHS:
            self.assertTrue(path.stock and path.angelo)
            self.assertIn(path.unit, ("per million vertices", "per check"))
        per_vertex = {path.id for path in reads.PATHS if path.per_vertex}
        self.assertEqual(per_vertex, {"positions", "vertex_group_weights"})

    def test_retired_reads_keep_their_last_numbers(self):
        self.assertFalse({entry["id"] for entry in reads.RETIRED} & {path.id for path in reads.PATHS})
        for entry in reads.RETIRED:
            self.assertTrue(entry["retired"] and entry["stock_route"] and entry["angelo_route"] and entry["note"])
            for measured in entry["measurements"]:
                self.assertGreater(measured["stock_ms"], 0)
                self.assertGreater(measured["angelo_ms"], 0)

    def test_the_fastest_supported_stock_route_is_the_comparison(self):
        entry = {"id": "positions", "label": "Vertex positions", "unit": "per million vertices", "per_vertex": True,
                 "vertices": 2_000_000, "note": "", "routes": [
                     {"side": "stock", "route": "slow", "supported": True, "median_ms": 40.0, "range_ms": [39, 41],
                      "instructions": 800, "parity": True},
                     {"side": "stock", "route": "fast", "supported": True, "median_ms": 2.0, "range_ms": [2, 3],
                      "instructions": 20, "parity": True},
                     {"side": "stock", "route": "hack", "supported": False, "median_ms": 1.0, "range_ms": [1, 1],
                      "instructions": 10, "parity": True},
                     {"side": "angelo", "route": "native", "supported": True, "median_ms": 3.0, "range_ms": [3, 4],
                      "instructions": None, "parity": True}]}
        row = web._row(entry)
        self.assertEqual((row["stock_route"], row["stock_ms"], row["angelo_ms"]), ("fast", 1.0, 1.5))
        self.assertEqual([other["route"] for other in row["stock_other_routes"]], ["slow", "hack"])
        self.assertIsNone(row["instructions"])   # Angelo's count is missing, so the pair is not published.
        self.assertTrue(row["parity"])


class NativeReadsIdentityTests(unittest.TestCase):
    """The native-reads report names the app's build as the Angelo commit, never the harness checkout's HEAD."""

    HARNESS = "a658eb9a2176" + "0" * 28
    APP = dict(binary_path="/Angelo.app/Contents/MacOS/Angelo", version_string="5.2.2 LTS")

    def setUp(self):
        with mock.patch.dict(sys.modules, {"bpy": SimpleNamespace()}):   # Imported outside Blender: bpy unused.
            sys.modules.pop("bench_native_reads", None)
            import bench_native_reads
        self.bench = bench_native_reads
        git = mock.patch.object(self.bench.subprocess, "run", return_value=SimpleNamespace(stdout=self.HARNESS + "\n"))
        git.start()
        self.addCleanup(git.stop)

    def test_the_binary_names_itself_and_the_harness_checkout_is_its_own_field(self):
        app = SimpleNamespace(build_hash=b"afaca1a036ca", build_branch=b"HEAD", **self.APP)
        angelo = self.bench.identity(app, native=True)
        self.assertEqual((angelo["build_hash"], angelo["build_branch"]), ("afaca1a036ca", "HEAD"))
        self.assertEqual((angelo["angelo_commit"], angelo["harness_commit"]), ("afaca1a036ca", self.HARNESS))
        stock = self.bench.identity(app, native=False)
        self.assertEqual((stock["build_hash"], stock["angelo_commit"]), ("afaca1a036ca", None))
        published = web.web_json(dict(angelo, machine={"cpu": "M4 Pro", "cores": 14, "platform": "macOS"},
                                      scene={"objects": 1969, "vertices": 2662867}, runs=7, paths=[]))
        self.assertEqual((published["angelo_commit"], published["harness_commit"]), ("afaca1a036ca", self.HARNESS))

    def test_a_binary_without_a_build_hash_is_refused_not_guessed(self):
        for unreadable in (b"Unknown", b"unknown", b"", None, "not a hash"):
            app = SimpleNamespace(build_hash=unreadable, **self.APP)
            with self.subTest(build_hash=unreadable), self.assertRaises(SystemExit) as refused:
                self.bench.identity(app, native=True)
            self.assertIn("/Angelo.app/Contents/MacOS/Angelo reports no build hash", str(refused.exception))
        with self.assertRaises(SystemExit):
            self.bench.identity(SimpleNamespace(**self.APP), native=True)   # No build_hash at all.


if __name__ == "__main__":
    unittest.main()
