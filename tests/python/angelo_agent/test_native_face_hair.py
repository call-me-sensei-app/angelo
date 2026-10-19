# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The face and hair one-shot tests without Blender: the mesher, the readings and the comparisons.

Shapes with known answers stand in for heads: a sphere's outline is a circle of its radius, a profile with a
bump has its landmark at the bump, a shifted shape reads as the shift. The registry and brief rules every
one-shot test follows are in ``test_native_oneshot_registry.py``; the graders inside Blender are measured by
``face_variants.py`` (stock Blender).
"""

import math
import sys
import unittest
from pathlib import Path

import numpy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import json  # noqa: E402
import tempfile  # noqa: E402

import face_casters  # noqa: E402
import face_report  # noqa: E402
import face_compare  # noqa: E402
import face_measure  # noqa: E402
import face_tests  # noqa: E402
import hair_kit  # noqa: E402
import head_designs  # noqa: E402
import head_sdf  # noqa: E402
import scenario_specs as specs  # noqa: E402

SPHERE = {"name": "sphere", "steps": [{"kind": "sphere", "centre": (0.0, 0.0, 0.0), "radius": 0.08}],
          "low": (-0.1, -0.1, -0.1), "high": (0.1, 0.1, 0.1)}
FRONT = {"location": (0.0, -2.0, 0.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 0.512}
SIDE = {"location": (-2.0, 0.0, 0.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 0.512}


def field_caster(design, camera):
    return face_casters.FieldCaster(face_casters.View(camera), {"Head": (face_casters.design_field(design),
                                                                         (design["low"], design["high"]))})


class MesherTests(unittest.TestCase):
    def test_a_design_meshes_closed_outward_and_on_its_surface(self):
        for name in ("portrait", "stylised"):
            with self.subTest(design=name):
                design = head_designs.design(name)
                vertices, quads = head_sdf.surface_nets(design, design["low"], design["high"], 0.002)
                self.assertGreater(head_sdf.signed_volume(vertices, quads), 0.0)   # Faces wind outward.
                edges = numpy.concatenate([quads[:, [0, 1]], quads[:, [1, 2]], quads[:, [2, 3]], quads[:, [3, 0]]])
                forward = edges[:, 0] * 10 ** 7 + edges[:, 1]
                backward = edges[:, 1] * 10 ** 7 + edges[:, 0]
                self.assertEqual(len(numpy.setdiff1d(forward, backward)), 0)      # Closed: every edge has a twin.
                off = numpy.abs(head_sdf.distance(design, vertices))
                # On the surface, except a few vertices of cells a 1 mm mouth groove crosses at this 2 mm cell size.
                self.assertLess(numpy.percentile(off, 99.9), 1e-4)
                self.assertLess(off.max(), 1e-3)

    def test_a_sphere_meshes_to_its_volume(self):
        vertices, quads = head_sdf.surface_nets(SPHERE, SPHERE["low"], SPHERE["high"], 0.002)
        self.assertAlmostEqual(head_sdf.signed_volume(vertices, quads), 4 / 3 * math.pi * 0.08 ** 3, delta=2e-5)

    def test_every_design_has_eyes_inside_its_box_and_seeds(self):
        for name, build in head_designs.DESIGNS.items():
            with self.subTest(design=name):
                design = build()
                for centre in head_designs.eye_centres(design):
                    self.assertTrue(all(low < value < high for low, value, high in
                                        zip(design["low"], centre, design["high"])))
                self.assertIn(name, head_designs.SEEDS)


class ReadingTests(unittest.TestCase):
    def test_a_sphere_reads_as_a_circle_of_its_radius(self):
        caster = field_caster(SPHERE, FRONT)
        us, vs = face_measure.grid((-0.08, 0.08, -0.08, 0.08), (-0.25, 0.25, -0.14, 0.14))
        depth, names = face_measure.raster(caster, us, vs)
        left, right = face_measure.outline(caster, us, vs, depth)
        rows = numpy.isfinite(left)
        expected = numpy.sqrt(numpy.maximum(0.08 ** 2 - vs[rows] ** 2, 0.0))
        # Bisection to 0.05 mm, except at the very top and bottom rows where the circle is nearly flat.
        middle = numpy.abs(vs[rows]) < 0.07
        self.assertLess(numpy.abs(right[rows][middle] - expected[middle]).max(), 6e-5)
        self.assertLess(numpy.abs(left[rows][middle] + expected[middle]).max(), 6e-5)
        self.assertEqual(set(names[numpy.isfinite(depth)].tolist()), {"Head"})
        self.assertAlmostEqual(float(numpy.nanmin(depth)), 2.0 - 0.08, delta=1e-5)

    def test_the_same_shape_reads_the_same_and_a_shift_reads_as_the_shift(self):
        def reading(design):
            caster = field_caster(design, FRONT)
            us, vs = face_measure.grid((-0.08, 0.08, -0.08, 0.08), (-0.25, 0.25, -0.14, 0.14))
            depth, _ = face_measure.raster(caster, us, vs)
            return {"us": us, "vs": vs, "depth": depth,
                    "contour": face_measure.contour(caster, us, vs, numpy.isfinite(depth))}
        truth = reading(SPHERE)
        radii = numpy.linalg.norm(truth["contour"], axis=1)
        self.assertLess(numpy.abs(radii - 0.08).max(), 6e-5)   # Every edge point on the circle, rows and columns.
        same = face_compare.outline(truth, truth, 0.0004)
        self.assertEqual((same["p95_mm"], same["iou"]), (0.0, 1.0))
        moved = reading(dict(SPHERE, steps=[dict(SPHERE["steps"][0], centre=(0.002, 0.0, 0.0))]))
        shifted = face_compare.outline(moved, truth, 0.0004)
        self.assertAlmostEqual(shifted["max_mm"], 2.0, delta=0.1)
        self.assertLess(shifted["iou"], 0.97)

    def test_a_profile_bump_is_found_where_it_is(self):
        vs = numpy.arange(0.05, -0.05, -0.00025)
        us = 0.08 + 0.01 * numpy.exp(-((vs + 0.01) / 0.004) ** 2) - 0.006 * numpy.exp(-((vs - 0.02) / 0.003) ** 2)
        found = face_measure.profile_landmarks(vs, us, {"pronasale": -0.012, "nasion": 0.018})
        self.assertAlmostEqual(found["pronasale"]["v"], -0.01, delta=2.5e-4)
        self.assertAlmostEqual(found["pronasale"]["u"], 0.09, delta=1e-6)
        self.assertAlmostEqual(found["nasion"]["v"], 0.02, delta=2.5e-4)
        self.assertFalse(found["pronasale"]["at_edge"] or found["nasion"]["at_edge"])
        flat = face_measure.extremum(vs, numpy.full(len(vs), 0.08), (-0.02, 0.0), "max")
        self.assertLess(flat["prominence"], face_measure.MIN_PROMINENCE)   # No feature: never well defined.

    def test_windows_lie_between_neighbours(self):
        spans = face_measure.windows({"a": 0.02, "b": 0.0, "c": -0.03})
        self.assertEqual(spans["b"], (-0.015, 0.01))
        self.assertEqual(spans["a"][0], 0.01)
        self.assertEqual(spans["c"][1], -0.015)

    def test_a_turn_below_finds_where_the_surface_falls_away(self):
        def depth_at(us, vs):   # A wall facing the viewer down to v = -0.02, then a step 5 cm back.
            return numpy.where(numpy.asarray(vs) > -0.02, 1.0, 1.05)
        found = face_measure.turn_below([0.0, 0.01], 0.0, depth_at)
        self.assertTrue(numpy.allclose(found, -0.02, atol=6e-5))   # Found to 0.05 mm.

    def test_strands_mark_the_pixels_they_cross_at_their_nearest_depth(self):
        view = face_casters.View(FRONT)
        us, vs = numpy.arange(-10, 11) * 0.001, numpy.arange(10, -11, -1) * 0.001
        near = numpy.array([[-0.008, -0.5, 0.0], [0.008, -0.5, 0.0]])
        far = numpy.array([[0.0, 0.5, 0.0], [0.0, 0.5, 0.0005]])
        depth = face_measure.strand_depths(view, [near, far], us, vs)
        row = int(numpy.argmin(numpy.abs(vs)))
        self.assertTrue(numpy.isfinite(depth[row, 2:19]).all())
        self.assertAlmostEqual(float(depth[row, 10]), 1.5, delta=1e-9)   # The nearer strand wins at the crossing.


class ReliefTests(unittest.TestCase):
    def test_relief_reads_both_ways_and_per_region(self):
        maps = {"side_to_truth": {"u": numpy.array([0.0, 0.01, 0.1]), "v": numpy.array([0.0, 0.0, 0.0]),
                                  "d": numpy.array([0.001, 0.003, 0.5])},
                "truth_to_side": {"u": numpy.array([0.0]), "v": numpy.array([0.0]), "d": numpy.array([0.002])}}
        found = face_compare.relief(maps, {"middle": [-0.02, 0.02, -0.01, 0.01], "empty": [0.5, 0.6, 0.5, 0.6]})
        self.assertEqual(found["middle"]["cells"], 3)                     # The far point lies outside the region.
        self.assertAlmostEqual(found["middle"]["mean_mm"], 2.0, places=6)
        self.assertEqual(found["empty"]["p90_mm"], float("inf"))           # No surface there: never a pass.


class ReportTests(unittest.TestCase):
    def test_the_report_reads_every_side_from_its_saved_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, "angelo-trial-01", "capture")
            capture.mkdir(parents=True)
            results = [
                {"kind": "face_outline", "passed": False, "details": {"view": "front", "p95_mm": 3.5}},
                {"kind": "face_outline", "passed": True, "details": {"view": "side", "p95_mm": 1.25}},
                {"kind": "face_landmarks", "passed": False, "details": {"landmarks": {
                    "pronasale": {"error_mm": 4.0, "forward_mm": 4.0, "up_mm": 0.0},
                    "nasion": {"error_mm": 0.5, "forward_mm": -0.5, "up_mm": 0.0}}}},
                {"kind": "hygiene", "passed": True, "details": {}}]
            (capture / "final-checks.json").write_text(json.dumps({"results": results}))
            Path(folder, "oneshot-scorecard.json").write_text(json.dumps({"test": "oneshot_head_portrait", "sides": [
                {"label": "Angelo 1", "verdict": "failed", "capture_folder": "angelo-trial-01/capture"},
                {"label": "Blender MCP 1", "verdict": "failed", "capture_folder": None}]}))
            rows = face_report.side_rows(folder)
        self.assertEqual([row["read"] for row in rows], [True, False])   # Nothing is guessed for an unread side.
        found = face_report.summary(rows[0])
        self.assertEqual((found["outline_front_p95_mm"], found["outline_side_p95_mm"]), (3.5, 1.25))
        self.assertEqual((found["worst_landmark"], found["worst_landmark_mm"]), ("pronasale", 4.0))
        text = face_report.markdown(rows)
        self.assertIn("| oneshot_head_portrait | Angelo 1 | failed | 3.50 / 1.25 | pronasale 4.00 |", text)
        self.assertIn("| oneshot_head_portrait | Blender MCP 1 | failed | not read |", text)


    def test_the_calibration_table_gives_the_floor_and_each_defect(self):
        outline = "the front outline within 2 mm of the front picture"
        table = [
            {"test": "t", "variant": "as built", "follows_the_brief": True, "as_expected": True, "failed": [],
             "readings": {outline: {"view": "front", "p95_mm": 0.06}}},
            {"test": "t", "variant": "remeshed", "follows_the_brief": True, "as_expected": True, "failed": [],
             "readings": {outline: {"view": "front", "p95_mm": 0.31}}},
            {"test": "t", "variant": "wider jaw", "follows_the_brief": False, "as_expected": True, "failed": [outline],
             "readings": {outline: {"view": "front", "p95_mm": 3.9}}}]
        text = face_report.calibration(table)
        self.assertIn("| outline front p95 | 0.310 |", text)          # The worst build that follows the brief.
        self.assertIn("| wider jaw | True | %s | outline front p95 3.90 |" % outline, text)
        self.assertIn("2 builds that follow the brief, 2 as expected; 1 defects, 1 as expected.", text)


class StartFileTests(unittest.TestCase):
    def test_a_one_shot_with_a_start_file_makes_it_first_and_records_it(self):
        """The start job runs before the reference job, which opens the start file; the manifest keeps it."""
        import oneshot_references
        import scenario_references
        from unittest import mock
        calls = []

        def run_job(blender, arguments, scratch, log, timeout=1800, script=None, blend=None):
            calls.append((Path(script).name, blend.name if blend else None))
            out = Path(arguments[arguments.index("--out") + 1])
            if Path(script).name == "scenario_start_job.py":
                (out / "start.json").write_text(json.dumps({"file": "start.blend", "sha256": "ab" * 32}))
                return 0
            module = specs.module("oneshot_hair_short")
            for view in module.CAMERAS:
                (out / (view + ".jpg")).write_bytes(b"jpeg " + view.encode())
            (out / "crops.json").write_text("{}")
            (out / "selfcheck.json").write_text(json.dumps({"success": True, "final": {"passed": 10, "total": 10}}))
            return 0

        with tempfile.TemporaryDirectory() as root, \
                mock.patch.dict("os.environ", {specs.REFERENCES_ENV: root}), \
                mock.patch.object(scenario_references, "run_job", side_effect=run_job), \
                mock.patch.object(scenario_references, "blender_version", return_value="test"):
            data = oneshot_references.make("oneshot_hair_short", "/blender")
            self.assertEqual(calls, [("scenario_start_job.py", None), ("oneshot_job.py", "start.blend")])
            self.assertEqual(data["start"], {"file": "start.blend", "sha256": "ab" * 32})
            self.assertEqual(specs.start_file("oneshot_hair_short")[1], "ab" * 32)   # What both sides append.

    def test_the_start_job_accepts_one_shot_names(self):
        self.assertIn("choices=specs.SCENARIO_IDS + specs.ONESHOT_NAMES", (HERE / "scenario_start_job.py").read_text())


class TestDefinitionTests(unittest.TestCase):
    def test_the_face_and_hair_group_and_their_sources(self):
        members = specs.ONESHOT_GROUPS["oneshot-face-hair"]
        self.assertEqual(len(members), 5)
        for task_set in members:
            name = specs.scenario_id(task_set)
            with self.subTest(test=name):
                module = specs.module(name)
                self.assertEqual(specs.ONESHOT_SOURCES[name], specs.FACE_HAIR_SOURCES)
                self.assertIn(module.FACE["design"], head_designs.DESIGNS)
                self.assertTrue({"front", "side", "top", "hero"} <= set(module.CAMERAS))
                for view in ("front", "side"):
                    self.assertEqual(module.CAMERAS[view]["target"][2], module.CAMERAS["front"]["target"][2])
                self.assertIn(face_tests.frame_text(module.CAMERAS["front"]["ortho_scale"],
                                                    module.CAMERAS["front"]["target"][2])[:60], module.PROMPT)
                kinds = {check["kind"] for check in module.TURNS[0]["checks"]}
                self.assertTrue(kinds & {"face_landmarks", "hair_shape"})
                if module.FACE.get("hair"):
                    self.assertIn("back", module.TURNS[0]["references"])
                    self.assertTrue(getattr(module, "START", None))

    def test_the_pictures_scale_is_stated_exactly(self):
        text = face_tests.frame_text(0.512, -0.015)
        self.assertIn("0.512 m across and 0.288 m tall", text)
        self.assertIn("0.4 mm a pixel", text)
        self.assertIn("z = -0.015", text)

    def test_hair_roots_lie_on_the_scalp_above_the_hairline(self):
        design = head_designs.design("portrait")
        roots = hair_kit.roots(design, 500, 3)
        self.assertEqual(len(roots), 500)
        self.assertLess(numpy.abs(head_sdf.distance(design, roots)).max(), 1e-6)
        self.assertTrue((roots[:, 2] > hair_kit.hairline_z(hair_kit.bearing(roots)) - 1e-9).all())

    def test_a_style_is_the_same_every_time(self):
        design = head_designs.design("portrait")
        first, radii = hair_kit.strands(design, "short")
        second, _ = hair_kit.strands(design, "short")
        self.assertTrue(numpy.array_equal(first, second))
        self.assertEqual(first.shape[1], hair_kit.STYLES["short"]["points"])
        self.assertTrue((head_sdf.distance(design, first.reshape(-1, 3)) > -1e-4).all())   # Never inside.



class ImportTests(unittest.TestCase):
    def test_helpers_are_imported_with_their_module_never_inside_a_function(self):
        """Blender MCP's grading prelude puts this folder on sys.path only while it imports the scenario and its
        checks (bench_mcp_grading._scenario_prelude); a helper imported later, inside a check or a setup, fails
        there with ModuleNotFoundError (the face and hair dry run's MCP side did). Modules the prelude itself
        imports stay importable anywhere."""
        import ast
        import re
        import bench_mcp_grading
        prelude = bench_mcp_grading._scenario_prelude("oneshot-head-portrait")
        preloaded = set(re.findall(r"\b(\w+)\b", " ".join(line for line in prelude.split("\n")
                                                             if line.strip().startswith("import "))))
        here = {path.stem for path in HERE.glob("*.py")}
        modules = (["face_casters", "face_compare", "face_measure", "face_truth", "face_tests", "hair_kit",
                    "head_checks", "head_designs", "head_kit", "head_sdf"]
                   + ["scenario_oneshot_" + name.replace("-", "_") for name in specs.ONESHOT_FACE_HAIR])
        for module in modules:
            tree = ast.parse((HERE / (module + ".py")).read_text())
            for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.Lambda))):
                for node in ast.walk(function):
                    names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                             else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                    late = [name for name in names if name in here and name not in preloaded]
                    with self.subTest(module=module, line=getattr(node, "lineno", 0)):
                        self.assertEqual(late, [])


if __name__ == "__main__":
    unittest.main()
