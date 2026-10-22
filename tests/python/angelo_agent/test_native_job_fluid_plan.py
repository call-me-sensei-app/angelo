# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Mantaflow bake's plan without Blender: grid, estimates, expected files, the disk outlook, unsafe paths.

The grid is Blender's own formula (``fluid.cc``) on domains of every shape and
tie; the estimates grow with cells, noise and particles as they must; the frame
check accepts every name Mantaflow itself accepts when it reads a frame and
nothing else; the path rule is ``BLI_path_make_safe``'s on both platforms. The
running app is covered by ``test_blender_fluid_jobs``.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_fluid_plan as plan  # noqa: E402


def blender_grid(size, res):
    """``manta_set_domain_from_mesh`` transcribed: the longest side (x first on ties) gets ``res``."""
    if size[0] >= max(size[1], size[2]):
        scale = res / size[0]
        return res, max(int(size[1] * scale + 0.5), 4), max(int(size[2] * scale + 0.5), 4)
    if size[1] >= max(size[0], size[2]):
        scale = res / size[1]
        return max(int(size[0] * scale + 0.5), 4), res, max(int(size[2] * scale + 0.5), 4)
    scale = res / size[2]
    return max(int(size[0] * scale + 0.5), 4), max(int(size[1] * scale + 0.5), 4), res


class Grid(unittest.TestCase):
    def test_matches_blenders_formula_on_many_shapes(self):
        shapes = [(2, 2, 2), (4, 1, 2), (1, 4, 2), (1, 2, 4), (0.1, 10, 0.1), (3, 3, 1), (1, 3, 3), (3, 1, 3),
                  (100, 0.5, 30), (0.02, 0.01, 0.015), (7.3, 7.3, 7.3), (5, 2.5, 5)]
        for size in shapes:
            for res in (6, 24, 32, 64, 97, 256):
                self.assertEqual(plan.grid(size, res), blender_grid(size, res), (size, res))

    def test_a_flat_domain_is_refused(self):
        for size in ((0, 1, 1), (1, 0, 1), (1, 1, -1)):
            with self.assertRaises(ValueError):
                plan.grid(size, 32)


class Estimates(unittest.TestCase):
    def test_grow_with_cells_noise_and_particles(self):
        small, large = plan.estimate((32, 32, 32), "GAS"), plan.estimate((64, 64, 64), "GAS")
        self.assertEqual(large["memory"], 8 * small["memory"])
        self.assertGreater(plan.estimate((32, 32, 32), "GAS", noise_scale=2)["memory"], small["memory"])
        self.assertEqual(plan.estimate((32, 32, 32), "GAS", noise_scale=1), small)   # Scale 1 adds no grid.
        liquid = plan.estimate((32, 32, 32), "LIQUID", particles_per_cell=2)
        self.assertGreater(plan.estimate((32, 32, 32), "LIQUID", particles_per_cell=8)["memory"], liquid["memory"])
        for value in (small, large, liquid):
            self.assertGreater(value["frame"], 0)
            self.assertGreater(value["memory"], value["frame"])


class Files(unittest.TestCase):
    def test_frame_names_as_mantaflow_writes_them(self):
        self.assertEqual(plan.file_frame("fluid_data_0001.vdb", "fluid_data"), 1)
        self.assertEqual(plan.file_frame("fluid_mesh_12345.bobj.gz", "fluid_mesh"), 12345)
        self.assertEqual(plan.file_frame("fluid_data_-005.vdb", "fluid_data"), -5)
        for name in ("fluid_data_1.vdb", "fluid_data_0001", "xfluid_data_0001.vdb", "fluid_noise_0001.vdb"):
            self.assertIsNone(plan.file_frame(name, "fluid_data"), name)

    def test_every_frame_of_every_cache_or_it_is_missing(self):
        expected = plan.expected_files(["data", "mesh"], (3, 5))
        staged = dict.fromkeys(["data/fluid_data_0003.vdb", "data/pp_0004.uni", "data/fluid_data_0005.vdb",
                                "mesh/fluid_mesh_0003.bobj.gz", "mesh/lMesh_0004.bobj.gz", "config/config_0003.uni"],
                               1000)
        self.assertEqual(plan.missing(expected, staged), {"mesh": [5]})
        self.assertEqual(plan.missing(expected, dict(staged, **{"mesh/fluid_mesh_0005.bobj.gz": 10})), {})
        # An empty file is a frame Mantaflow could not write: missing, not cached.
        self.assertEqual(plan.missing(expected, dict(staged, **{"mesh/fluid_mesh_0005.bobj.gz": 0})), {"mesh": [5]})
        # A file in the wrong folder, or another cache's name, never counts.
        self.assertEqual(plan.missing(plan.expected_files(["data"], (1, 1)), {"mesh/fluid_data_0001.vdb": 9}),
                         {"data": [1]})
        self.assertEqual(plan.frames_written(list(staged)), 3)

    def test_the_names_are_mantaflows_own(self):
        source = (ROOT / "source/blender/makesdna/DNA_fluid_types.h").read_text(encoding="utf-8")
        for folder, names in plan.CACHES.values():
            self.assertIn('"%s"' % folder, source)
            for name in names:
                self.assertIn('"%s"' % name, source, name)

    def test_the_file_bound_follows_each_caches_format(self):
        frames = (1, 1000)
        self.assertLess(plan.most_files(["data", "mesh"], frames),
                        plan.most_files(["data", "mesh"], frames, one_file_per_frame=False))
        self.assertGreaterEqual(plan.most_files(["data"], frames), 2 * 1000)   # A config and a data file a frame.
        # A liquid with mesh, speed vectors and particles: config, data, mesh, its .vdb of speed vectors, particles.
        self.assertGreaterEqual(plan.most_files(["data", "mesh", "particles"], frames, mesh_files=2), 5 * 1000)
        # The mesh has its own format: a Uni data cache never multiplies the mesh's files.
        self.assertEqual(plan.most_files(["mesh"], frames, one_file_per_frame=False),
                         plan.most_files(["mesh"], frames))


class Disk(unittest.TestCase):
    def test_measured_bytes_per_frame_decide(self):
        gib = plan.GiB
        self.assertEqual(plan.disk_outlook(10 * gib, 1, 100, 1), (0, True))       # Not measured yet.
        self.assertEqual(plan.disk_outlook(2 * gib, 2, 10, 13 * gib), (10 * gib, True))
        self.assertEqual(plan.disk_outlook(2 * gib, 2, 10, 11 * gib), (10 * gib, False))   # The margin counts.
        self.assertEqual(plan.disk_outlook(2 * gib, 2, 0, 0), (0, True))           # Nothing left to write.


class UnsafePaths(unittest.TestCase):
    def test_posix(self):
        self.assertIsNone(plan.unsafe_path("/Users/a/My Project/caches/smoke"))
        self.assertIsNone(plan.unsafe_path("/Users/a/プロジェクト/caches/煙"))
        for bad in ("a:b", "what?", "star*", "pipe|", 'quote"', "less<", "more>", "back\\slash", "tab\tname", "..."):
            self.assertEqual(plan.unsafe_path("/Users/a/%s/caches" % bad), bad)

    def test_windows(self):
        self.assertIsNone(plan.unsafe_path("C:\\Users\\a\\My Project\\caches", windows=True))
        self.assertIsNone(plan.unsafe_path("D:/work/aux_files/x", windows=True))
        for bad in ("aux", "AUX.txt", "com1", "trailing.", "con"):
            self.assertEqual(plan.unsafe_path("C:\\Users\\a\\%s\\caches" % bad, windows=True), bad)

    def test_the_rule_is_blenders_and_stricter_only_for_a_backslash(self):
        source = (ROOT / "source/blender/blenlib/intern/path_utils.cc").read_text(encoding="utf-8")
        self.assertIn('"/\\\\?*:|\\""', source)
        self.assertIn('#define INVALID_TOKENS "<>"', source)


class Approval(unittest.TestCase):
    FACTS = {"objects": ["Smoke"], "fluid": "gas", "cells": [64, 32, 64], "voxel": 0.03125, "size": [2, 1, 2],
             "frames": [1, 120], "cache_kinds": ["data", "noise"], "memory_gib": 0.9, "disk_gib_at_most": 12.5,
             "disk_free_gib": 300.0, "flows": ["Smoke Flow"], "outside": ["Far Flow"], "replay": True,
             "earlier_cache": True}

    def message(self, facts):
        from angelo_agent.approvals import approval_message
        return approval_message("angelo_job_start", {"kind": "BAKE_SIMULATION", "target": "scn:1:2",
                                                     "output": "caches/smoke", "_facts": facts})

    def test_states_what_the_bake_will_use_and_write(self):
        message = self.message(self.FACTS)
        body = message["body"]
        self.assertIn("smoke", message["title"].lower())
        for text in ("64×32×64 cells", "0.03125 per cell", "frames 1–120", "data, noise", "0.9 GiB", "12.5 GiB",
                     "300.0 GiB free", "Smoke Flow", "Outside the domain: Far Flow", "Replay to All",
                     "earlier cache stays on disk", "caches/smoke"):
            self.assertIn(text, body)
        self.assertNotIn("geometry-nodes", message["title"])
        self.assertNotIn("physics cache", body)   # Never the physics bake's lines.

    def test_liquid_title_and_no_optional_lines(self):
        facts = dict(self.FACTS, fluid="liquid")
        for key in ("outside", "replay", "earlier_cache"):
            del facts[key]
        message = self.message(facts)
        self.assertIn("liquid", message["title"].lower())
        self.assertNotIn("Outside the domain", message["body"])
        self.assertNotIn("Replay", message["body"])
        self.assertNotIn("no cache folder", message["body"])

    def test_a_domain_without_a_cache_folder_is_told_it_gets_one(self):
        body = self.message(dict(self.FACTS, no_cache_folder=True))["body"]
        self.assertIn("no cache folder of its own; this bake gives it one in the output", body)


class FieldRefusals(unittest.TestCase):
    def test_a_refused_field_names_the_kinds_that_take_it(self):
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent.job_spec import FIELDS, JobRequestError, validate_job
        cases = [({"kind": "BAKE_PHYSICS", "target": "scn:1:2", "output": "sims/x"}, "BAKE_PHYSICS",
                  {"BAKE_TEXTURE", "BAKE_SIMULATION"}),
                 ({"kind": "RENDER_STILL", "output": "a.png", "frame_end": 9}, "RENDER_STILL",
                  {"RENDER_ANIMATION", "EXPORT_USD", "EXPORT_ALEMBIC"}),
                 ({"kind": "EXPORT_USD", "output": "a.usd", "bake_type": "AO"}, "EXPORT_USD", {"BAKE_TEXTURE"})]
        for request, kind, takers in cases:
            with self.subTest(request=request), self.assertRaises(JobRequestError) as caught:
                validate_job("angelo_job_start", request)
            text = str(caught.exception)
            self.assertTrue(text.startswith(kind + " does not take: "), text)
            named = set(text.split("; kinds that take them: ")[1].split(", "))
            self.assertEqual(named, takers)
            self.assertTrue(all(set(request) - {"kind"} - FIELDS[kind] <= FIELDS[other] for other in named))
        # A field no kind takes: the refusal says only that.
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", {"kind": "BAKE_PHYSICS", "colour": 1})
        self.assertEqual(str(caught.exception), "BAKE_PHYSICS does not take: colour")


class Module(unittest.TestCase):
    def test_no_blender_import(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/job_fluid_plan.py").read_text(encoding="utf-8"))
        names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                 for alias in node.names}
        self.assertFalse({"bpy", "mathutils"} & names)


if __name__ == "__main__":
    unittest.main()
