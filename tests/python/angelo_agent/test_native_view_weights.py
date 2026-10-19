# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""WEIGHT and ATTRIBUTE shadings of ``angelo_view`` on the host (doc/angelo/view_sources.rst).

The weight ramp, the weights read from vertex groups, the deformed geometry and
the shading are pure NumPy over stand-ins; ``view_capture``'s dispatch runs
over the stand-ins of ``test_native_view_capture`` with the GPU draw replaced.
GPU pixels are proven by ``test_blender_view_sources`` in the compiled app.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import view_cache, view_weights  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from test_native_view_capture import HANDLE, FakeOffScreen, environment  # noqa: E402


class Groups(list):
    def __init__(self, names, active=0):
        super().__init__(SimpleNamespace(name=name, index=index) for index, name in enumerate(names))
        self.active = self[active] if names else None

    def get(self, name):
        return next((group for group in self if group.name == name), None)


class Array(list):
    def __init__(self, values):
        super().__init__(range(len(values)))
        self.values = numpy.asarray(values)

    def foreach_get(self, _attribute, out):
        out[:] = self.values.reshape(-1)


class Vertices(Array):
    def __init__(self, positions, memberships):
        super().__init__(positions)
        self.records = [SimpleNamespace(index=index, groups=[SimpleNamespace(group=g, weight=w) for g, w in pairs])
                        for index, pairs in enumerate(memberships)]

    def __iter__(self):
        return iter(self.records)


TRIANGLE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
UP = [(0.0, 0.0, 1.0)] * 3


def weighted_object(memberships, *, evaluated_positions=None, edit=False, groups=("Spine", "Neck"), kind="MESH"):
    data = SimpleNamespace(vertices=Vertices(TRIANGLE, memberships), vertex_normals=Array(UP),
                           loop_triangles=Array([(0, 1, 2)]), is_editmode=edit)
    evaluated_mesh = SimpleNamespace(vertices=Array(evaluated_positions or [(p[0], p[1], 2.0) for p in TRIANGLE]),
                                     vertex_normals=Array(UP), loop_triangles=Array([(0, 1, 2)]))
    cleared = []
    evaluated = SimpleNamespace(matrix_world=[[1, 0, 0, 10], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                                to_mesh=lambda: evaluated_mesh, to_mesh_clear=lambda: cleared.append(True))
    obj = SimpleNamespace(name="Body", type=kind, data=data, vertex_groups=Groups(list(groups)),
                          evaluated_get=lambda _depsgraph: evaluated, cleared=cleared)
    return obj


WINDOW = SimpleNamespace(view_layer=SimpleNamespace(depsgraph=None))


class WeightDataTests(unittest.TestCase):
    def test_the_ramp_is_blenders_and_unassigned_is_black(self):
        colours = view_weights.ramp(numpy.array([0.0, 0.25, 0.5, 0.75, 1.0, numpy.nan, 1.5], dtype=numpy.float32))
        self.assertEqual(colours.tolist(), [[0, 0, 1], [0, 1, 1], [0, 1, 0], [1, 1, 0], [1, 0, 0], [0, 0, 0], [1, 0, 0]])

    def test_weights_come_from_the_named_or_active_group_with_unassigned_vertices_marked(self):
        obj = weighted_object([[(0, 0.2)], [(1, 0.9)], [(0, 1.0), (1, 0.4)]])
        weights, name = view_weights.group_weights(obj, None)
        self.assertEqual(name, "Spine")
        self.assertEqual([None if numpy.isnan(w) else round(float(w), 3) for w in weights], [0.2, None, 1.0])
        weights, name = view_weights.group_weights(obj, "Neck")
        self.assertEqual([None if numpy.isnan(w) else round(float(w), 3) for w in weights], [None, 0.9, 0.4])
        with self.assertRaisesRegex(RuntimeError, r"'Body' has no vertex group 'Tail'. Its groups: 'Spine', 'Neck'"):
            view_weights.group_weights(obj, "Tail")
        obj.vertex_groups = Groups([])
        with self.assertRaisesRegex(RuntimeError, "no vertex group that is active. Its groups: none"):
            view_weights.group_weights(obj, None)

    def test_geometry_is_the_deformed_evaluated_mesh_in_world_space_and_its_copy_is_cleared(self):
        obj = weighted_object([[(0, 0.5)], [], []])
        geometry = view_weights.target_geometry(WINDOW, obj, None)
        self.assertEqual(geometry["positions"][1].tolist(), [11.0, 0.0, 2.0], "evaluated positions moved by the matrix")
        self.assertEqual(geometry["indices"].tolist(), [[0, 1, 2]])
        self.assertEqual(geometry["facts"], {"group": "Spine", "vertices": 3, "assigned": 1, "deformed": True,
                                             "edit_mode": False, "minimum": 0.5, "maximum": 0.5})
        self.assertEqual(obj.cleared, [True])
        changed = weighted_object([[], [], []], evaluated_positions=[(0, 0, 0)] * 4)
        undeformed = view_weights.target_geometry(WINDOW, changed, None)
        self.assertFalse(undeformed["facts"]["deformed"])
        self.assertEqual(undeformed["positions"][1].tolist(), [11.0, 0.0, 0.0], "the original mesh instead")
        self.assertEqual(changed.cleared, [True])
        with self.assertRaisesRegex(RuntimeError, "'Body' is a CURVE"):
            view_weights.target_geometry(WINDOW, weighted_object([[], [], []], kind="CURVE"), None)

    def test_faces_turned_from_the_camera_are_darker(self):
        geometry = {"positions": numpy.zeros((2, 3), numpy.float32), "weights": numpy.array([1.0, 1.0], numpy.float32),
                    "normals": numpy.array([[0, 0, 1], [1, 0, 0]], numpy.float32)}
        top = {"projection": "ORTHOGRAPHIC", "matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]]}
        colours = view_weights.shaded_colours(geometry, top)
        self.assertEqual(colours[0].tolist(), [1.0, 0.0, 0.0, 1.0], "square-on: the ramp colour itself")
        self.assertAlmostEqual(float(colours[1][0]), view_weights.AMBIENT, places=6)
        perspective = dict(top, projection="PERSPECTIVE")
        self.assertEqual(view_weights.shaded_colours(geometry, perspective)[0].tolist(), [1.0, 0.0, 0.0, 1.0])


class ShadingDispatchTests(unittest.TestCase):
    def setUp(self):
        self.env = environment()
        self.modules = patch.dict(sys.modules, {"bpy": self.env.bpy, "gpu": self.env.gpu,
                                                "mathutils": self.env.mathutils})
        self.modules.start()
        sys.modules.pop("angelo_agent.view_capture", None)
        self.capture = importlib.import_module("angelo_agent.view_capture")
        self.queries = patch.object(self.capture, "scene_queries", self.env.queries)
        self.queries.start()
        self.directory = tempfile.TemporaryDirectory()
        self.references = ReferenceStore(self.directory.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        FakeOffScreen.draws, FakeOffScreen.fail = [], False
        view_cache.clear()

    def tearDown(self):
        view_cache.clear()
        self.queries.stop()
        self.modules.stop()
        self.directory.cleanup()

    def run_view(self, **args):
        return self.capture.execute(self.env.window, dict({"targets": [HANDLE], "views": ["FRONT"]}, **args),
                                    self.references)

    def test_attribute_draws_the_active_colour_attribute_and_restores_the_viewport(self):
        shading = self.env.space.shading
        before = shading.snapshot()
        result = self.run_view(shading="ATTRIBUTE")
        self.assertEqual(FakeOffScreen.draws[0]["type"], "SOLID")
        self.assertEqual(shading.snapshot(), before)
        self.assertIn("color_type", shading.writes, "the Attribute colour type was set for the draw")
        self.assertIn("active colour attribute", result["note"])
        self.assertEqual(result["shading"], "ATTRIBUTE")

    def test_weight_views_are_drawn_by_the_host_at_the_planned_cameras_and_never_cached(self):
        geometry = {"positions": numpy.zeros((3, 3), numpy.float32), "normals": numpy.zeros((3, 3), numpy.float32),
                    "indices": numpy.array([[0, 1, 2]], numpy.int32), "weights": numpy.ones(3, numpy.float32),
                    "facts": {"group": "Spine", "vertices": 3, "assigned": 3, "deformed": False, "edit_mode": False}}
        cameras = []

        def draw(_geometries, camera, width, height):
            cameras.append(camera["source"])
            return bytes(width * height * 4)
        self.env.target.data = SimpleNamespace(vertices=[None] * 3)
        with patch.object(view_weights, "target_geometry", lambda window, obj, group: geometry), \
                patch.object(view_weights, "_draw", draw):
            first = self.run_view(shading="WEIGHT", views=["FRONT", "TOP"], group="Spine")
            second = self.run_view(shading="WEIGHT", views=["FRONT", "TOP"], group="Spine")
        self.assertEqual(cameras, ["AXIS:FRONT", "AXIS:TOP"] * 2, "drawn again: weight views are never cached")
        self.assertEqual(FakeOffScreen.draws, [], "the viewport is not drawn")
        self.assertEqual(first["weights"], [geometry["facts"]])
        self.assertEqual([entry["code"] for entry in first["uncertainty"]], ["weights_undeformed"])
        self.assertIn("Blender shows them only in Weight Paint", first["note"])
        self.assertFalse(any(view["cache_hit"] for view in second["views"]))
        self.assertEqual([view["shading"] for view in first["views"]], ["WEIGHT", "WEIGHT"])
        self.assertEqual(Path(first["views"][0]["path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_too_many_vertices_are_refused_before_any_is_read(self):
        self.env.target.data = SimpleNamespace(vertices=[None] * (view_weights.MAX_VERTICES + 1))
        with patch.object(view_weights, "target_geometry", side_effect=AssertionError("read")) as read:
            with self.assertRaisesRegex(RuntimeError, "more than 500000 vertices"):
                self.run_view(shading="WEIGHT")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
