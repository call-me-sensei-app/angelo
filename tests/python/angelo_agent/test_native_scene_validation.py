# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure schema/reader/recipe tests; the companion Blender test executes kernels."""

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_recipes import expand_recipe, recipe_capabilities, validate_recipe
from angelo_agent.scene_validation import VerificationContext, execute, observe, verify
from angelo_agent.validation_spec import validate_checks, validate_validation, MAX_OBJECTS, MAX_VERTICES


class Collection(list):
    def get(self, name):
        return next((value for value in self if value.name == name), None)


def cube():
    coordinates = [(-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
                   (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1)]
    faces = [(0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6), (0, 1, 3, 2), (4, 6, 7, 5)]
    edges = sorted({tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])})
    layers = Collection()
    layers.active = None
    mesh = NS(vertices=[NS(co=list(co), groups=[]) for co in coordinates],
              edges=[NS(vertices=e) for e in edges], polygons=[NS(vertices=f, material_index=0) for f in faces],
              loops=list(range(24)), uv_layers=layers)
    return NS(type="MESH", name="Bound cube", mode="OBJECT", data=mesh,
              matrix_world=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
              location=[0, 0, 0], rotation_euler=[0, 0, 0], rotation_mode="XYZ", scale=[1, 1, 1],
              material_slots=[], modifiers=Collection(), constraints=[], parent=None,
              parent_type="OBJECT", parent_bone="", vertex_groups=Collection(), animation_data=None)


class Curve:
    def __init__(self, index, start, end):
        self.data_path, self.array_index = "location", index
        self.keyframe_points = [NS(co=[1, start]), NS(co=[10, end])]
        self.modifiers, self.mute, self.is_valid = [], False, True

    def evaluate(self, frame):
        start, end = self.keyframe_points
        return start.co[1] + (end.co[1] - start.co[1]) * (frame - 1) / 9


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.object = cube()
        self.context = VerificationContext({"$cube": self.object}, [self.object])

    def check(self, kind, **fields):
        return verify(self.context, [{"kind": kind, "target": "$cube", **fields}])

    def test_dimensions_honor_world_matrix_and_tolerance(self):
        self.assertTrue(self.check("dimensions", expected=[2, 2, 2], tolerance=0)["passed"])
        self.object.matrix_world[0][0] = 2
        self.assertTrue(self.check("dimensions", expected=[4, 2, 2], tolerance=0)["passed"])
        self.assertEqual(self.check("dimensions", expected=[2, 2, 2], tolerance=0)["status"], "fail")
        self.assertTrue(self.check("dimensions", expected=[4, 2, 2], tolerance=0, space="LOCAL")["passed"])

    def test_local_dimensions_are_blenders_dimensions_with_the_scale_applied(self):
        # A footbridge one-shot scaled the default cube to [2, 0.6, 0.12] as the deck, checked LOCAL against the
        # 4 x 1.2 x 0.24 m the observation calls its dimensions. The check measured the unscaled 2 m mesh, failed,
        # and a correction round followed.
        self.object.matrix_world = [[2, 0, 0, 0], [0, 0.6, 0, 0], [0, 0, 0.12, 0], [0, 0, 0, 1]]
        result = self.check("dimensions", expected=[4, 1.2, 0.24], tolerance=1e-9, space="LOCAL")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["checks"][0]["observed"]["space"], "LOCAL")
        self.assertEqual(self.check("dimensions", expected=[2, 2, 2], tolerance=1e-9, space="LOCAL")["status"], "fail")
        # Turned 90 degrees about Z: LOCAL stays along the object's own axes, WORLD follows the world's.
        self.object.matrix_world = [[0, -0.6, 0, 1], [2, 0, 0, 0], [0, 0, 0.12, 0], [0, 0, 0, 1]]
        self.assertTrue(self.check("dimensions", expected=[4, 1.2, 0.24], tolerance=1e-9, space="LOCAL")["passed"])
        self.assertTrue(self.check("dimensions", expected=[1.2, 4, 0.24], tolerance=1e-9)["passed"])
        # A negative (mirrored) scale measures as its size, as Blender's dimensions do.
        self.object.matrix_world = [[-2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertTrue(self.check("dimensions", expected=[4, 2, 2], tolerance=1e-9, space="LOCAL")["passed"])
        self.object.matrix_world = [[math.nan, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertEqual(self.check("dimensions", expected=[2, 2, 2], tolerance=1, space="LOCAL")["status"], "fail")

    def test_closed_cube_and_open_surface(self):
        self.assertTrue(self.check("mesh_manifold", expected="CLOSED")["passed"])
        self.object.data.polygons.pop()
        self.object.data.edges = [e for e in self.object.data.edges if set(e.vertices) != {5, 7}]
        self.assertFalse(self.check("mesh_manifold", expected="CLOSED")["passed"])
        plane = cube()
        plane.data.polygons = [plane.data.polygons[0]]
        face = plane.data.polygons[0].vertices
        plane.data.vertices = [plane.data.vertices[i] for i in face]
        plane.data.polygons[0].vertices = (0, 1, 2, 3)
        plane.data.edges = [NS(vertices=e) for e in ((0, 1), (1, 2), (2, 3), (3, 0))]
        plane.data.loops = list(range(4))
        self.context.targets["$plane"] = plane
        result = verify(self.context, [{"kind": "mesh_manifold", "target": "$plane", "expected": "OPEN"}])
        self.assertTrue(result["passed"])

    def test_degeneracy_is_not_silently_ignored(self):
        self.assertTrue(self.check("mesh_degenerate", max_faces=0, max_edges=0, epsilon=0)["passed"])
        self.object.data.vertices[1].co = self.object.data.vertices[0].co[:]
        result = self.check("mesh_degenerate", max_faces=0, max_edges=0, epsilon=0.0000001)
        self.assertFalse(result["passed"])
        self.assertGreater(result["checks"][0]["observed"]["zero_length_edges"], 0)

    def test_closed_edge_incidence_does_not_hide_disconnected_vertex_fans(self):
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0), (0, -1, 0), (0, 0, -1)]
        faces = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3), (0, 5, 4), (0, 4, 6), (4, 5, 6), (5, 0, 6)]
        edges = {tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])}
        self.object.data.vertices = [NS(co=list(v), groups=[]) for v in vertices]
        self.object.data.edges = [NS(vertices=e) for e in edges]
        self.object.data.polygons = [NS(vertices=f) for f in faces]
        self.object.data.loops = list(range(24))
        result = self.check("mesh_manifold", expected="CLOSED")
        self.assertEqual(result["status"], "fail")
        metrics = result["checks"][0]["observed"]
        self.assertEqual(metrics["nonmanifold_edges"], 0)
        self.assertEqual(metrics["nonmanifold_vertices"], 1)

    def test_duplicate_measurements_share_a_bounded_read_but_never_mutate(self):
        from angelo_agent import scene_validation
        args = {"kind": "dimensions", "target": "$cube", "expected": [2, 2, 2], "tolerance": 0}
        # Cube base scan is 50 elements; one world bound scan is eight more.
        with patch.object(scene_validation, "MAX_TOTAL_ELEMENTS", 58):
            self.assertTrue(verify(self.context, [args] * 32)["passed"])
        with patch.object(scene_validation, "MAX_TOTAL_ELEMENTS", 57):
            self.assertEqual(verify(self.context, [args])["status"], "inconclusive")

    def test_nonfinite_data_fails_without_json_nan(self):
        self.object.data.vertices[0].co[0] = math.nan
        result = self.check("finite_coordinates")
        self.assertEqual(result["status"], "fail")
        json.dumps(result, allow_nan=False)

    def test_missing_target_fails_and_names_are_not_resolved(self):
        result = verify(self.context, [{"kind": "object_type", "target": "Bound cube", "expected": "MESH"}])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][0]["observed"], {"exists": False})

    def test_unsupported_geometry_is_inconclusive_not_pass(self):
        self.object.type = "CURVE"
        self.assertEqual(self.check("finite_coordinates")["status"], "inconclusive")
        self.object.type = "MESH"
        self.object.mode = "EDIT"
        self.assertEqual(self.check("mesh_manifold", expected="CLOSED")["status"], "inconclusive")

    def test_oversized_geometry_and_scene_are_inconclusive(self):
        class Huge:
            def __len__(self):
                return MAX_VERTICES + 1

            def __iter__(self):
                raise AssertionError("Oversized geometry must never be traversed")
        self.object.data.vertices = Huge()
        self.assertEqual(self.check("finite_coordinates")["status"], "inconclusive")
        self.context.scene_objects = [self.object] * (MAX_OBJECTS + 1)
        self.assertEqual(verify(self.context, [{"kind": "object_count", "minimum": 0, "maximum": MAX_OBJECTS}])["status"], "inconclusive")

    def test_counts_require_a_complete_scene_overlay(self):
        check = {"kind": "object_count", "minimum": 1, "maximum": 1, "types": ["MESH"]}
        self.assertTrue(verify(self.context, [check])["passed"])
        self.context.scene_objects = None
        self.assertEqual(verify(self.context, [check])["status"], "inconclusive")

    def test_material_assignment_checks_actual_face_slots(self):
        material = NS(name="Approved material")
        self.object.material_slots = [NS(material=material)]
        self.assertTrue(self.check("material_assignment", material=material.name, all_faces=True)["passed"])
        self.object.data.polygons[1].material_index = 1
        self.assertFalse(self.check("material_assignment", material=material.name, all_faces=True)["passed"])

    def test_uv_checks_presence_and_finiteness(self):
        self.assertFalse(self.check("uv_layer", require_nonempty=True, require_finite=True)["passed"])
        layer = NS(name="UVMap", uv=[NS(vector=[0, 0]) for _ in self.object.data.loops])
        self.object.data.uv_layers.append(layer)
        self.object.data.uv_layers.active = layer
        self.assertTrue(self.check("uv_layer", require_nonempty=True, require_finite=True)["passed"])
        layer.uv[0].vector[0] = math.inf
        self.assertFalse(self.check("uv_layer", require_nonempty=True, require_finite=True)["passed"])

    def test_modifier_properties_and_parent_are_closed(self):
        self.object.modifiers.append(NS(name="Bevel", type="BEVEL", width=0.125, segments=2))
        self.assertTrue(self.check("modifier", name="Bevel", type="BEVEL", values={"width": 0.125, "segments": 2}, tolerance=0)["passed"])
        parent = cube()
        self.context.targets["$parent"] = parent
        self.object.parent = parent
        self.assertTrue(self.check("parent", expected="$parent")["passed"])
        self.assertFalse(self.check("parent", expected=None)["passed"])

    def test_direct_animation_samples_and_unsupported_nla(self):
        curves = [Curve(i, 0, i + 1) for i in range(3)]
        self.object.animation_data = NS(action=NS(fcurves=curves), drivers=[], nla_tracks=[])
        args = {"property": "location", "samples": [{"frame": 10, "expected": [1, 2, 3]}], "tolerance": 0, "require_keyframes": True}
        self.assertTrue(self.check("animation", **args)["passed"])
        args["samples"][0]["expected"][1] = 9
        self.assertFalse(self.check("animation", **args)["passed"])
        self.object.animation_data.nla_tracks.append(NS(strips=[object()]))
        self.assertEqual(self.check("animation", **args)["status"], "inconclusive")

    def test_require_keyframes_means_the_channels_are_keyed_not_a_key_at_every_sample(self):
        # A LINEAR turn keyed at its ends, sampled between the keys, failed with require_keyframes although
        # its values were right.
        curves = [Curve(i, 0, 9 * (i + 1)) for i in range(3)]
        self.object.animation_data = NS(action=NS(fcurves=curves), drivers=[], nla_tracks=[])
        args = {"property": "location", "samples": [{"frame": 5, "expected": [4, 8, 12]}], "tolerance": 1e-9,
                "require_keyframes": True}
        result = self.check("animation", **args)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"][0]["observed"]["keyframed"])
        args["samples"][0]["expected"] = [4, 8, 12.5]
        self.assertFalse(self.check("animation", **args)["passed"], "Values are still compared")
        curves[2].keyframe_points = []
        args["samples"][0]["expected"] = [4, 8, 12]
        curves[2].evaluate = lambda frame: 12
        self.assertFalse(self.check("animation", **args)["passed"], "A channel without keys is not keyframed")
        self.assertTrue(self.check("animation", **dict(args, require_keyframes=False))["passed"])

    def test_dimensions_with_modifiers_are_the_evaluated_ones_observations_show(self):
        # Five 1 m spheres 1.5 m apart (an Array) are 7 m long, as the observation's dimensions said; the check
        # measured the 1 m base mesh and failed a correct plan.
        self.object.bound_box = [[x, y, z] for x in (-0.5, 6.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
        self.object.modifiers.append(NS(name="Array", type="ARRAY", show_viewport=True))
        result = self.check("dimensions", expected=[7, 1, 1], tolerance=1e-9)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["checks"][0]["observed"]["scope"], "EVALUATED_BOUNDS")
        self.assertTrue(self.check("dimensions", expected=[7, 1, 1], tolerance=1e-9, space="LOCAL")["passed"])
        self.object.matrix_world = [[0, -2, 0, 5], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]  # 90 degrees, y scaled 2
        self.assertTrue(self.check("dimensions", expected=[2, 7, 1], tolerance=1e-9)["passed"])
        self.assertTrue(self.check("dimensions", expected=[7, 2, 1], tolerance=1e-9, space="LOCAL")["passed"],
                        "LOCAL: the evaluated bounds along the object's axes, its scale applied")
        c, s = math.cos(0.3), math.sin(0.3)
        self.object.matrix_world = [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertEqual(self.check("dimensions", expected=[7, 1, 1], tolerance=1e-9)["status"], "inconclusive")
        self.object.modifiers[0].show_viewport = False
        self.assertTrue(self.check("dimensions", expected=[2, 2, 2], tolerance=1e-9, space="LOCAL")["passed"],
                        "A disabled modifier leaves the base mesh measured")

    def test_observation_is_aggregate_bound_and_readonly(self):
        self.context.bindings["$cube"] = {"handle": "fixture-handle", "revision": "a" * 64}
        before = copy.deepcopy([v.co for v in self.object.data.vertices])
        result = observe(self.context, "$cube")
        payload = json.dumps(result)
        self.assertEqual(result["revision"], "a" * 64)
        self.assertNotIn('"coordinates"', payload)
        self.assertNotIn('"faces": [', payload)
        self.assertEqual(before, [v.co for v in self.object.data.vertices])
        self.assertTrue(result["read_only"])


class ValidationContractTests(unittest.TestCase):
    def test_strict_limits_and_no_predicate_escape_hatch(self):
        good = {"kind": "dimensions", "target": "$cube", "expected": [1, 2, 3], "tolerance": 0.001}
        for changed in (dict(good, code="exec(1)"), dict(good, expected=[math.nan, 1, 1]),
                        dict(good, tolerance=True), dict(good, tolerance=10 ** 1000),
                        dict(good, kind="arbitrary_rna"), dict(good, target="x\x7f")):
            with self.subTest(changed=repr(changed)[:120]), self.assertRaises(ValueError):
                validate_checks([changed])
        for checks in ([], [good] * 33):
            with self.assertRaises(ValueError):
                validate_checks(checks)
        with self.assertRaises(ValueError):
            validate_checks([{"kind": "modifier", "target": "$cube", "name": "Bevel", "type": "BEVEL", "values": {"arbitrary_rna": "evil"}, "tolerance": 0}])

    def test_results_and_normalized_arguments_are_independent(self):
        source = [{"kind": "dimensions", "target": "$cube", "expected": [1, 2, 3], "tolerance": 0}]
        result = validate_checks(source)
        result[0]["expected"][0] = 8
        self.assertEqual(source[0]["expected"], [1, 2, 3])

    def test_public_checks_require_handle_revision_binding(self):
        handle = "scn:" + "a" * 32 + ":1"
        args = {"read_set": [{"handle": handle, "revision": "b" * 64}], "checks": [{"kind": "object_type", "target": handle, "expected": "MESH"}]}
        self.assertEqual(validate_validation("angelo_scene_verify", args)["checks"], args["checks"])
        args["checks"][0]["target"] = "Cube"
        with self.assertRaises(ValueError):
            validate_validation("angelo_scene_verify", args)

    def test_public_unknown_revision_evidence_is_inconclusive_not_a_pass(self):
        fake = ModuleType("angelo_agent.scene_queries")
        class Unavailable(ValueError):
            pass
        fake.SceneQueryError = Unavailable
        fake.resolve_object = lambda window, handle: cube()
        def unavailable(window, obj):
            raise Unavailable("original_state_work_budget")
        fake.object_revision = unavailable
        handle = "scn:" + "a" * 32 + ":1"
        args = {"read_set": [{"handle": handle, "revision": "b" * 64}],
                "checks": [{"kind": "object_type", "target": handle, "expected": "MESH"}]}
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": fake}):
            result = execute(NS(), "angelo_scene_verify", args)
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["binding_verified"])
        self.assertFalse(result["passed"])


class RecipeTests(unittest.TestCase):
    def test_exact_expansions_are_canonical_bounded_and_copy_parameters(self):
        inputs = {"bevel-and-shade": {"width": 0.1, "segments": 2},
                  "clean-mesh": {"vertex_indices": [0, 1], "distance": 0.001},
                  "material-setup": {"name": "Recipe material", "base_color": [0.2, 0.3, 0.4, 1]},
                  "simple-animation": {"property": "location", "start_frame": 1, "end_frame": 20, "start_value": [0, 0, 0], "end_value": [2, 0, 0]},
                  "uv-prep": {"method": "CUBE"}}
        for name, params in inputs.items():
            with self.subTest(recipe=name):
                before = copy.deepcopy(params)
                expanded = expand_recipe(name, "$cube", params)
                self.assertEqual(expanded, expand_recipe(name, "$cube", params))
                self.assertLessEqual(len(expanded["operations"]), 2)
                self.assertEqual(expanded["correction_policy"]["automatic_retries"], 0)
                self.assertTrue(expanded["requires_program_approval"])
                self.assertEqual(params, before)
                json.dumps(expanded, allow_nan=False)

    def test_analytic_fit_obeys_explicit_bounds_and_never_retries(self):
        obj = cube()
        observed = observe(VerificationContext({"$cube": obj}), "$cube")
        params = {"dimensions": [4, 2, 1], "minimum_scale": 0.1, "maximum_scale": 4, "maximum_scale_ratio": 2}
        expanded = expand_recipe("fit-dimensions", "$cube", params, observed)
        self.assertEqual(expanded["operations"][0]["parameters"]["scale"], [2, 1, 0.5])
        params["maximum_scale_ratio"] = 1.5
        with self.assertRaises(ValueError):
            expand_recipe("fit-dimensions", "$cube", params, observed)
        observed["constraints_count"] = 1
        with self.assertRaises(ValueError):
            expand_recipe("fit-dimensions", "$cube", params, observed)
        with self.assertRaises(ValueError):
            expand_recipe("fit-dimensions", "$cube", params)

    def test_unstated_fit_bounds_are_the_widest_a_transform_takes_and_still_refuse(self):
        """Recorded GPT fit-dimensions calls stated exactly these bounds; they are what an unstated bound means."""
        from angelo_agent.action_recipes import FIT_BOUNDS, validate_parameters
        self.assertEqual(FIT_BOUNDS, {"minimum_scale": 0.000001, "maximum_scale": 1000, "maximum_scale_ratio": 100})
        self.assertEqual({key: validate_parameters("fit-dimensions", {"dimensions": [1, 2, 1]})[key] for key in FIT_BOUNDS},
                         FIT_BOUNDS)
        observed = observe(VerificationContext({"$cube": cube()}), "$cube")
        expanded = expand_recipe("fit-dimensions", "$cube", {"dimensions": [4, 2, 1]}, observed)
        self.assertEqual(expanded["operations"][0]["parameters"]["scale"], [2, 1, 0.5])
        with self.assertRaises(ValueError):  # A ratio past 100 is refused before approval, stated or not.
            expand_recipe("fit-dimensions", "$cube", {"dimensions": [400, 2, 1]}, observed)
        with self.assertRaises(ValueError):  # A scale past a transform's 1,000.
            expand_recipe("fit-dimensions", "$cube", {"dimensions": [2001, 2, 1], "maximum_scale_ratio": 100}, observed)

    def test_recipe_schemas_and_public_names_do_not_authorize_mutation(self):
        self.assertEqual(len(recipe_capabilities()["recipes"]), 6)
        with self.assertRaises(ValueError):
            expand_recipe("clean-mesh", "$cube", {"distance": 0.01})
        with self.assertRaises(ValueError):
            expand_recipe("uv-prep", "$cube", {"method": "CUBE", "code": "delete()"})
        with self.assertRaises(ValueError):
            validate_recipe("angelo_expand_recipe", {"recipe": "uv-prep", "target": "$cube", "revision": "a" * 64, "parameters": {"method": "CUBE"}})

    def test_recipe_tool_has_the_outer_closed_object_routing_shape(self):
        from angelo_agent.tool_specs import validate_call
        handle = "scn:" + "a" * 32 + ":1"
        args = {"recipe": "uv-prep", "target": handle, "revision": "a" * 64, "parameters": {"method": "CUBE"}}
        checked = validate_call("angelo_expand_recipe", args)
        self.assertEqual(checked["parameters"]["name"], "UVMap")


class BevelApplyScaleTests(unittest.TestCase):
    """bevel-and-shade's apply_scale: the scale baked first in the recipe's own program, or refused before approval.

    Without it a typed plan beveled a scaled cube as it was (a stretched bevel) or spent a lookup and an exec round
    on transform_apply; a script that applies the scale before beveling gets an even bevel.
    """
    HANDLE, REVISION = "scn:" + "a" * 32 + ":7", "b" * 64
    BEVEL = {"width": 0.125, "segments": 3}

    def facts(self, **changes):
        return dict({"scale": [2, 0.5, 1.5], "type": "MESH", "children": 0, "delta_transforms": False,
                     "delta_scale": False, "outside_scale": False, "animated_scale": False, "constraints": 0,
                     "modifiers": []}, **changes)

    def expand(self, parameters, facts=None):
        observation = None if facts is None else {"target": self.HANDLE, "status": "pass", "revision": self.REVISION,
                                                  "scale_facts": facts}
        return expand_recipe("bevel-and-shade", self.HANDLE, parameters, observation)

    def complete_checks(self, expanded):
        from angelo_agent.program_checks import required_checks
        from angelo_agent.program_spec import validate_program
        binding = [{"handle": self.HANDLE, "revision": self.REVISION}]
        return required_checks(validate_program("angelo_scene_program", {
            "mode": "APPLY", "read_set": binding, "write_set": binding, "operations": expanded["operations"],
            "expected_checks": expanded["expected_checks"]}))

    @staticmethod
    def scale_checks(checks):
        return [check for check in checks if check["kind"] == "transform" and check["property"] == "scale"]

    def test_without_it_the_expansion_is_unchanged(self):
        plain = self.expand(dict(self.BEVEL))
        self.assertEqual(plain, self.expand(dict(self.BEVEL, apply_scale=False)))
        scaled = self.expand(dict(self.BEVEL, apply_scale=False), self.facts())  # A scaled target is left as it is.
        self.assertEqual((scaled["operations"], scaled["expected_checks"]), (plain["operations"], plain["expected_checks"]))
        self.assertEqual([op["kind"] for op in plain["operations"]], ["modifier_add", "geometry_nodes"])
        self.assertEqual([check["kind"] for check in plain["expected_checks"]],
                         ["finite_coordinates", "modifier", "smooth_shading_graph"])
        self.assertFalse(plain["parameters"]["apply_scale"])
        self.assertEqual(self.scale_checks(self.complete_checks(plain)), [])

    def test_with_it_the_scale_is_applied_first_and_every_check_states_the_result(self):
        for scale in ([2, 0.5, 1.5], [3, 3, 3], [-2, 1, 1]):  # Non-uniform, uniform, mirrored (normals flip).
            with self.subTest(scale=scale):
                expanded = self.expand(dict(self.BEVEL, apply_scale=True), self.facts(scale=scale))
                apply, bevel, shade = expanded["operations"]
                self.assertEqual((apply["kind"], apply["target"], apply["parameters"]),
                                 ("transform_apply", self.HANDLE, {"location": False, "rotation": False, "scale": True}))
                self.assertEqual((bevel["kind"], bevel["parameters"]["settings"]["width"]), ("modifier_add", 0.125))
                self.assertEqual(shade["kind"], "geometry_nodes")
                self.assertEqual(self.scale_checks(expanded["expected_checks"])[0]["expected"], [1, 1, 1])
                complete = self.complete_checks(expanded)
                (scale_check,) = self.scale_checks(complete)
                self.assertEqual(scale_check["expected"], [1, 1, 1])
                kinds = [check["kind"] for check in complete]
                self.assertIn("smooth_shading_graph", kinds)
                self.assertTrue(any(check["kind"] == "modifier" and check.get("values", {}).get("width") == 0.125
                                    for check in complete))

    def test_a_unit_scale_adds_nothing(self):
        expanded = self.expand(dict(self.BEVEL, apply_scale=True), self.facts(scale=[1, 1.0000001, 1]))
        self.assertEqual(expanded["operations"], self.expand(dict(self.BEVEL))["operations"])
        self.assertEqual(self.scale_checks(expanded["expected_checks"]), [])

    def test_what_baking_would_get_wrong_is_refused_before_approval(self):
        cases = {"CHILDREN": {"children": 1}, "DELTA_TRANSFORMS": {"delta_transforms": True},
                 "ANIMATED_SCALE": {"animated_scale": True}, "CONSTRAINED": {"constraints": 2},
                 "NOT_A_MESH": {"type": "CURVE"}, "ZERO_SCALE": {"scale": [2, 0, 1]}}
        for code, change in cases.items():
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code + ".*without apply_scale|" + code):
                self.expand(dict(self.BEVEL, apply_scale=True), self.facts(**change))
        for observation in (None, {"target": self.HANDLE, "status": "fail", "scale_facts": self.facts()},
                            {"target": "scn:" + "c" * 32 + ":7", "status": "pass", "scale_facts": self.facts()},
                            {"target": self.HANDLE, "status": "pass", "scale_facts": self.facts(scale=[2, math.nan, 1])}):
            with self.subTest(observation=repr(observation)[:60]), self.assertRaises(ValueError):
                expand_recipe("bevel-and-shade", self.HANDLE, dict(self.BEVEL, apply_scale=True), observation)
        with self.assertRaises(ValueError):
            self.expand(dict(self.BEVEL, apply_scale="yes"), self.facts())

    def test_facts_are_read_from_the_object_as_transform_apply_judges_it(self):
        from angelo_agent.action_recipes import scale_facts

        def obj(**changes):
            basis = [[2, 0, 0, 0], [0, 0.5, 0, 0], [0, 0, 1.5, 0], [0, 0, 0, 1]]
            fields = dict(scale=(2, 0.5, 1.5), type="MESH", children=(), constraints=(), animation_data=None,
                          delta_location=(0, 0, 0), delta_rotation_euler=(0, 0, 0), delta_scale=(1, 1, 1),
                          delta_rotation_quaternion=(1, 0, 0, 0), modifiers=(), matrix_basis=basis, matrix_world=basis)
            fields.update(changes)
            return NS(**fields)

        def animation(paths=(), drivers=(), tracks=()):
            bag = NS(fcurves=[NS(data_path=path) for path in paths])
            action = NS(layers=[NS(strips=[NS(channelbag=lambda slot: bag)])])
            return NS(action=action, action_slot=object(), drivers=[NS(data_path=path) for path in drivers],
                      nla_tracks=list(tracks))

        self.assertEqual(scale_facts(obj()), self.facts())
        self.assertTrue(scale_facts(obj(children=(object(),)))["children"])
        self.assertTrue(scale_facts(obj(delta_scale=(1, 2, 1)))["delta_transforms"])
        self.assertTrue(scale_facts(obj(constraints=(object(),)))["constraints"])
        self.assertTrue(scale_facts(obj(delta_scale=(1, 2, 1)))["delta_scale"])
        self.assertFalse(scale_facts(obj(delta_location=(0, 0, 1)))["delta_scale"])
        self.assertEqual(scale_facts(obj(modifiers=(NS(type="SUBSURF"), NS(type="ARRAY"))))["modifiers"],
                         ["SUBSURF", "ARRAY"])
        stretched = [[6, 0, 0, 0], [0, 0.5, 0, 0], [0, 0, 1.5, 0], [0, 0, 0, 1]]  # A parent scaled (3, 1, 1).
        self.assertTrue(scale_facts(obj(matrix_world=stretched))["outside_scale"])
        self.assertFalse(scale_facts(obj(animation_data=animation(paths=("location",))))["animated_scale"])
        for animated in (animation(paths=("scale",)), animation(paths=("delta_scale",)),
                         animation(drivers=("scale",)), animation(tracks=(object(),))):
            self.assertTrue(scale_facts(obj(animation_data=animated))["animated_scale"])

    def test_the_note_is_where_models_read_recipes_and_costs_one_line(self):
        from angelo_agent.action_recipes import APPLY_SCALE_NOTE, recipe_capabilities
        from angelo_agent.tool_specs import codex_tools, native_plan_schema
        self.assertLessEqual(len(APPLY_SCALE_NOTE.encode()), 100)
        (bevel,) = [recipe for recipe in recipe_capabilities()["recipes"] if recipe["name"] == "bevel-and-shade"]
        self.assertIn(APPLY_SCALE_NOTE, bevel["description"])
        self.assertIn("transform_apply", bevel["expanded_kinds"])
        self.assertEqual(json.dumps(native_plan_schema()).count(json.dumps(APPLY_SCALE_NOTE)), 1, "once in Claude's plan")
        sent = [tool for tool in codex_tools(short_identifiers=True, defer=True) if tool.get("name") == "angelo_apply_recipe"]
        self.assertEqual(json.dumps(sent).count(json.dumps(APPLY_SCALE_NOTE)), 1, "once in the Codex recipe tool")


if __name__ == "__main__":
    unittest.main()
