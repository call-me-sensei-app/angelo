# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The typed ``uv_unwrap`` action refuses a dense mesh before it starts, like the other superlinear operators.

A stand-in ``bpy`` and a synthetic grid mesh run the real kernel up to the point where it would enter Edit
mode. native_coverage.rst ("operator_run work bounds") holds the measurements behind the number; the unbilled
Blender receipt is ``benchmark_blender_uv_unwrap_work.py``.
"""

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent  # noqa: E402,F401  (the package the stand-in submodules attach to)


class Reached(Exception):
    """Raised where the real kernel would enter Edit mode and call bpy.ops.uv.unwrap."""


class Collection(list):
    """A bpy_prop_collection stand-in: elements, a length and foreach_get over named columns."""

    def __init__(self, items=(), columns=None):
        super().__init__(items)
        self.columns = columns or {}

    def foreach_get(self, attribute, array):
        array[:] = self.columns[attribute]


class Layers:
    def __init__(self):
        self.created, self.active = [], None

    def __len__(self):
        return len(self.created)

    def get(self, name):
        return None

    def new(self, name):
        self.created.append(name)
        return SimpleNamespace(name=name)


def grid(side, selectable=True):
    """A quad grid of ``side`` x ``side`` faces as Blender stores it: face after face, vertices and edges shared.

    ``selectable=False`` keeps vertices and edges as bare indexes (their count is all a refusal reads).
    """
    x, y = np.meshgrid(np.arange(side), np.arange(side))
    x, y = x.ravel(), y.ravel()
    vertex = lambda dx, dy: (y + dy) * (side + 1) + x + dx
    horizontal = lambda dy: (y + dy) * side + x  # Edge (x, y)-(x + 1, y); there are side * (side + 1).
    vertical = lambda dx: side * (side + 1) + y * (side + 1) + x + dx
    loops_vertex = np.stack([vertex(0, 0), vertex(1, 0), vertex(1, 1), vertex(0, 1)], axis=1).ravel().astype(np.int32)
    loops_edge = np.stack([horizontal(0), vertical(1), horizontal(1), vertical(0)], axis=1).ravel().astype(np.int32)
    counts = ((side + 1) ** 2, 2 * side * (side + 1), side * side)
    items = lambda count: [SimpleNamespace(index=i, select=False) for i in range(count)] if selectable else range(count)
    return SimpleNamespace(
        vertices=Collection(items(counts[0])), edges=Collection(items(counts[1])),
        polygons=Collection([SimpleNamespace(index=i, select=False) for i in range(counts[2])],
                            {"loop_total": np.full(counts[2], 4, np.int32)}),
        loops=Collection(range(4 * counts[2]), {"vertex_index": loops_vertex, "edge_index": loops_edge}),
        uv_layers=Layers(), is_editmode=False)


class UnwrapBoundTests(unittest.TestCase):
    def setUp(self):
        package = sys.modules["angelo_agent"]
        before = set(vars(package))  # Submodules imported against the stand-in leave the package again.
        self.addCleanup(lambda: [delattr(package, name) for name in set(vars(package)) - before])
        bpy = SimpleNamespace(ops=SimpleNamespace(uv=SimpleNamespace(unwrap=lambda **kwargs: self.fail("ran the unwrap"))),
                              types=SimpleNamespace(), data=SimpleNamespace(), context=SimpleNamespace())
        patcher = patch.dict(sys.modules, {"bpy": bpy, "bmesh": SimpleNamespace(), "mathutils": SimpleNamespace(Vector=tuple)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kernel = importlib.import_module("angelo_agent.action_mesh")

    def unwrap(self, mesh, **parameters):
        """Run the kernel on ``mesh``; True when it got as far as Edit mode, else the refusal's text."""
        operation = {"kind": "uv_unwrap", "target": "$grid", "parameters": {
            "method": "ANGLE_BASED", "margin": .001, "uv_map": "UVMap", "margin_method": "SCALED", "fill_holes": True,
            "correct_aspect": False, "face_indices": [], "seam_edge_indices": [], **parameters}}
        context = SimpleNamespace(assert_staged=lambda staged: staged)
        with patch.object(self.kernel, "_mesh", return_value=mesh), \
                patch.object(self.kernel, "_edit_override", side_effect=Reached), \
                patch.object(self.kernel, "_readback", return_value={}):
            try:
                self.kernel._uv_unwrap(context, SimpleNamespace(data=mesh), operation)
            except Reached:
                return True
            except ValueError as error:
                return str(error)

    def test_work_counts_the_mesh_and_what_the_selected_faces_touch(self):
        mesh = grid(10)  # 11 x 11 vertices, 220 edges, 100 faces.
        everything = list(mesh.polygons)
        self.assertEqual(self.kernel._unwrap_work(mesh, everything), (121 + 220 + 100, 121 + 220 + 100))
        row = mesh.polygons[:10]  # One row: 10 faces, 22 vertices, 31 edges.
        self.assertEqual(self.kernel._unwrap_work(mesh, row), (441, 10 + 22 + 31))
        self.assertEqual(self.kernel._unwrap_work(mesh, mesh.polygons[:1]), (441, 1 + 4 + 4))

    def test_a_mesh_at_the_bound_unwraps_and_one_past_it_is_refused_before_anything_starts(self):
        within, past = grid(110), grid(120)  # 48,841 and 58,081 elements, all selected: 2.4e9 and 3.4e9.
        self.assertTrue(self.unwrap(within))
        self.assertEqual(within.uv_layers.created, ["UVMap"])
        refusal = self.unwrap(past)
        self.assertRegex(refusal, r"^WORK_BOUND: uv_unwrap takes time growing faster than linearly with its input: "
                                  r"58081 elements with 58081 selected")
        self.assertIn("uv_smart_project", refusal)  # The verified fallback for a dense mesh.
        self.assertEqual(past.uv_layers.created, [])  # Refused before it created a map or touched a selection.
        self.assertFalse(any(item.select for sequence in (past.vertices, past.edges, past.polygons) for item in sequence))

    def test_both_methods_and_the_native_budget_are_bounded(self):
        for method in ("ANGLE_BASED", "CONFORMAL"):
            with self.subTest(method=method):
                self.assertIn("WORK_BOUND", self.unwrap(grid(314, selectable=False), method=method))  # 98,596 vertices: the native budget.

    def test_a_small_selection_on_a_dense_mesh_is_not_refused(self):
        mesh = grid(120)
        self.assertIn("WORK_BOUND", self.unwrap(mesh))
        self.assertTrue(self.unwrap(grid(120), face_indices=list(range(100))))

    def test_the_bound_is_one_documented_number_near_two_seconds(self):
        self.assertEqual(self.kernel.UNWRAP_BOUND, 2.5e9)
        text = (Path(__file__).resolve().parents[3] / "doc/angelo/native_coverage.rst").read_text()
        self.assertIn("``uv_unwrap``", text.split("operator_run work bounds", 1)[1])


if __name__ == "__main__":
    unittest.main()
