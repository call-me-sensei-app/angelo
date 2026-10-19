# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometry an operation changes but no check can see is an unverified effect (``derived_geometry``).

Dependency-free. Regressions: a named box was certified "Host-verified"
beside an unchecked modifier apply, join, conversion, data operator, curve
rebuild, flattened curve and resized lattice, and a subdivide or delete
measured only by dimensions it does not change; selection, slot and shading
operators were partial for nothing. The rule follows each operation's mutation
families and what each kind of check can see. test_blender_partial_checks.py
runs the same programs in the compiled app.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import derived_geometry  # noqa: E402
from angelo_agent.action_operator_spec import ALLOWED  # noqa: E402
from angelo_agent.action_schemas import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.program_checks import resolve_obligations  # noqa: E402
from angelo_agent.program_completion import pure_program_checks  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":9"
CURVE = "scn:" + "a" * 32 + ":11"
LATTICE = "scn:" + "a" * 32 + ":13"
REV = "b" * 64
BOX = {"kind": "create_primitive", "target": None,
       "parameters": {"primitive": "CUBE", "name": "Marker", "alias": "m", "size": 1, "location": [6, 0, 0]}}
COUNTS = {key: {"minimum": 0, "maximum": 200} for key in ("vertices", "edges", "faces")}


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations, checks=()):
    return validate_program("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "expected_checks": list(checks), "operations": operations,
        "write_set": [{"handle": handle, "revision": REV} for handle in (OBJ, OTHER, CURVE, LATTICE)]})


def unknowns(operations, checks=()):
    return [(row["operation"], row["reason"], row["root"]["target"])
            for row in resolve_obligations(program(operations, checks)).unknowns()]


def dims(target, expected):
    return {"kind": "dimensions", "target": target, "expected": expected, "tolerance": .01}


SUBSURF = [op("modifier_add", type="SUBSURF", name="Sub", settings={"levels": 2}), op("modifier_apply", modifier="Sub")]
EXTRUDE = op("mesh_extrude", face_selector={"normal": [0, 0, 1]}, translation=[0, 0, 2.0])


class GeometryRuleTests(unittest.TestCase):
    def test_each_unchecked_geometry_change_beside_a_named_box_is_partial(self):
        cases = {
            "modifier_apply": (SUBSURF, 2, OBJ),
            "object_join": ([op("object_join", sources=[OTHER])], 1, OBJ),
            "object_convert": ([op("object_convert", None, source=CURVE, alias="cm", keep_original=False)], 1, "$cm"),
            "operator_run": ([op("operator_run", operator="mesh.subdivide", properties={"number_cuts": 2})], 1, OBJ),
            "mesh_extrude_on_a_new_object": ([op("mesh_extrude", "$m", face_selector={"normal": [0, 0, 1]},
                                                 translation=[0, 0, 2.0])], 1, "$m"),
            "mesh_recalculate_normals": ([op("mesh_recalculate_normals", face_indices=[0])], 1, OBJ),
            "curve_made_2d": ([op("property_set", CURVE, values={"data.dimensions": "2D"})], 1, CURVE),
            "lattice_resized": ([op("property_set", LATTICE, values={"data.points_u": 3})], 1, LATTICE),
            "lattice_outside_only": ([op("property_set", LATTICE, values={"data.use_outside": True})], 1, LATTICE),
            "shape_key_removed": ([op("operator_run", operator="object.shape_key_remove")], 1, OBJ),
        }
        for name, (operations, index, target) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(unknowns([BOX] + operations), [(index, derived_geometry.REASON, target)])
                self.assertIsNone(pure_program_checks(program([BOX] + operations)), "Partial obligations certify nothing")

    def test_every_effect_class_must_be_observed(self):
        """Regression: moves, smoothing, sorting and flipped normals measured only by counts."""
        degenerate = {"kind": "mesh_degenerate", "target": OBJ, "max_faces": 0, "max_edges": 0, "epsilon": 1e-8}
        counts = {"kind": "mesh_counts", "target": OBJ, **COUNTS}
        translate = op("operator_run", operator="transform.translate", properties={"value": [0, 0, 1]})
        smooth = op("operator_run", operator="mesh.vertices_smooth")
        for operation in (translate, smooth, op("operator_run", operator="transform.rotate", properties={"value": 1.57})):
            with self.subTest(operator=operation["parameters"]["operator"]):
                self.assertTrue(unknowns([operation], [counts, degenerate]), "Counts cannot see moved positions")
                self.assertTrue(unknowns([operation], [counts, dims(OBJ, [2, 2, 3])]),
                                "Nor can bounds: a turned face, a shifted mesh, a vertex moved inward (round 5)")
        resize_all = op("operator_run", operator="transform.resize", properties={"value": [2, 2, 2]}, select="ALL")
        self.assertEqual(unknowns([resize_all], [dims(OBJ, [4, 4, 4])]), [], "Scaling everything changes the extent only")
        for name, parameters in (("some elements", {"select": "KEEP"}),
                                 ("a mirror", {"properties": {"value": [-1, 1, 1]}}),
                                 ("another pivot", {"properties": {"value": [2, 2, 2], "center_override": [5, 0, 0]}})):
            with self.subTest(resize=name):
                self.assertTrue(unknowns([dict(resize_all, parameters=dict(resize_all["parameters"], **parameters))],
                                         [dims(OBJ, [4, 4, 4])]), "Scaling %s changes the shape too" % name)
        for name in ("mesh.sort_elements", "mesh.flip_normals", "transform.tilt", "mesh.bisect"):
            with self.subTest(operator=name):
                self.assertTrue(unknowns([op("operator_run", operator=name)], [counts, degenerate, dims(OBJ, [2, 2, 2])]),
                                "No check observes order, winding or point data")
        row = resolve_obligations(program([translate], [counts])).unknowns()[0]["affected"][0]
        self.assertEqual((row["unobserved"], row["checked_by"]), (["shape"], ["curve_counts with positions"]))
        # A curve's point move is seen only by stated positions; a lattice's by nothing.
        curve_move = op("operator_run", CURVE, operator="transform.translate", properties={"value": [0, 0, 1]})
        counted = {"kind": "curve_counts", "target": CURVE, "points": {"minimum": 3, "maximum": 3}}
        self.assertTrue(unknowns([curve_move], [counted]))
        self.assertEqual(unknowns([curve_move], [dict(counted, positions=[[0, 0, 1], [1, 0, 1], [2, 0, 1]])]), [])

    def test_a_check_that_can_see_every_class_completes_it(self):
        both = [{"kind": "mesh_counts", "target": OBJ, **COUNTS}, dims(OBJ, [2, 2, 4])]
        self.assertTrue(unknowns([BOX] + SUBSURF, both), "An applied modifier's shape is not observable")
        self.assertTrue(unknowns([BOX, op("object_convert", None, source=CURVE, alias="cm")],
                                 [{"kind": "mesh_manifold", "target": "$cm", "expected": "OPEN"}, dims("$cm", [2, 1, 1])]))
        # An extrude's topology is derived from its staged result (and proved live); dimensions see its extent,
        # even with batch copies after it.
        self.assertEqual(unknowns([EXTRUDE, op("batch", None, sources=[OBJ], array=[{"step": [0, 3, 0], "count": 3}])],
                                  [dims(OBJ, [2, 2, 4])]), [])
        self.assertTrue(unknowns([EXTRUDE], [{"kind": "mesh_counts", "target": OBJ, **COUNTS}]),
                        "Counts cannot see its displacement")
        # A flattened curve is seen by its points' stated positions (no bounds check reads a curve).
        flat = [op("property_set", CURVE, values={"data.dimensions": "2D"})]
        positions = {"kind": "curve_counts", "target": CURVE, "curves": {"minimum": 1, "maximum": 1},
                     "positions": [[0, 0, 0], [1, 0, 0]]}
        self.assertEqual(unknowns(flat, [positions]), [])
        self.assertTrue(unknowns(flat, [{"kind": "curve_counts", "target": CURVE, "curves": {"minimum": 1, "maximum": 1}}]),
                        "Counts cannot see flattened points")
        # A resized lattice regenerates point data no check reads: never certified.
        self.assertTrue(unknowns([op("property_set", LATTICE, values={"data.points_u": 3})],
                                 [{"kind": "curve_counts", "target": LATTICE, "points": {"minimum": 12, "maximum": 12}}]))
        self.assertTrue(unknowns([BOX] + SUBSURF, [{"kind": "mesh_counts", "target": "$m", **COUNTS}]),
                        "Another object's measurement measures nothing here")

    def test_dimensions_cannot_see_a_topology_change(self):
        """A top face subdivided or deleted keeps the bounds: only a count, manifold or degenerate check sees it."""
        for operation in (op("mesh_subdivide", face_selector={"normal": [0, 0, 1]}, cuts=2),
                          op("mesh_delete", face_selector={"normal": [0, 0, 1]}),
                          op("object_join", sources=[OTHER])):
            with self.subTest(kind=operation["kind"]):
                self.assertTrue(unknowns([operation], [dims(OBJ, [2, 2, 2])]))
                self.assertEqual(unknowns([operation], [{"kind": "mesh_degenerate", "target": OBJ, "max_faces": 0,
                                                         "max_edges": 0, "epsilon": 1e-8}]), [])
        # A dimensions check on a curve is inconclusive when it runs, so it refuses rather than certifies.

    def test_a_bend_moves_the_shape_that_no_check_reads(self):
        """mesh_bend cuts (topology), widens its bounds along up (extent) and moves every vertex (shape)."""
        bend = op("mesh_bend", rise=.3)
        counts = {"kind": "mesh_counts", "target": OBJ, **COUNTS}
        self.assertTrue(unknowns([bend], [dims(OBJ, [2, 2, 2.3]), counts]), "Nothing reads a mesh's shape")
        canonical = program([bend])["operations"][0]
        self.assertEqual(derived_geometry.effects(canonical),
                         {derived_geometry.TOPOLOGY, derived_geometry.EXTENT, derived_geometry.SHAPE})
        uncut = program([op("mesh_bend", rise=.3, segments=1)])["operations"][0]
        self.assertEqual(derived_geometry.effects(uncut), {derived_geometry.EXTENT, derived_geometry.SHAPE})

    def test_a_keyed_point_rewrite_is_refused_before_approval(self):
        with self.assertRaisesRegex(ValueError, "NOT_ANIMATABLE"):
            program([op("property_keyframe", LATTICE, values={"data.points_w": 3}, frame=4)])
        with self.assertRaisesRegex(ValueError, "NOT_ANIMATABLE"):
            program([op("property_keyframe", values={"active_material_index": 1}, frame=4)])

    def test_curve_points_state_their_own_counts_and_positions(self):
        operations = [BOX, op("curve_points", CURVE, points=[[0, 0, 0], [1, 1, 0], [2, 0, 0]])]
        self.assertEqual(unknowns(operations), [])
        stated = [check for check in resolve_obligations(program(operations)).checks() if check["kind"] == "curve_counts"]
        self.assertEqual(stated, [{"kind": "curve_counts", "target": CURVE, "points": {"minimum": 3, "maximum": 3},
                                   "curves": {"minimum": 1, "maximum": 1},
                                   "positions": [[0., 0., 0.], [1., 1., 0.], [2., 0., 0.]]}])
        # A later operator may change the splines: the stated counts no longer hold, so both are unverified.
        later = operations + [op("operator_run", CURVE, operator="curve.subdivide", properties={"number_cuts": 1})]
        self.assertEqual(unknowns(later), [(1, derived_geometry.REASON, CURVE), (2, derived_geometry.REASON, CURVE)])

    def test_writers_that_keep_or_state_geometry_and_non_geometry_kinds_stay_complete(self):
        for operations in ([op("camera_configure", settings={"lens": 35})], [op("transform_apply", scale=True)],
                           [op("transform_apply", rotation=True)],
                           [op("property_set", CURVE, values={"data.extrude": .2, "data.resolution_u": 24})],
                           [op("uv_smart_project", uv_map="UVMap")], [op("modifier_add", type="BEVEL", name="Bv")],
                           [op("operator_run", operator="object.forcefield_toggle")],
                           [op("operator_run", operator="object.shade_smooth")],
                           [op("operator_run", operator="object.vertex_group_add")],
                           [op("operator_run", operator="object.material_slot_add")],
                           [op("operator_run", operator="uv.unwrap")],
                           [op("operator_run", operator="mesh.select_all", properties={"action": "SELECT"})],
                           [op("keyframe_insert", property="location", frame=10, value=[0, 0, 1])]):
            with self.subTest(operation=operations[0]["parameters"].get("operator", operations[0]["kind"])):
                self.assertEqual(unknowns([BOX] + operations), [])

    def test_every_kind_is_classified_and_new_writers_default_to_unverified(self):
        """A kind writing a geometry family is unverified unless GEOMETRY_KEEPS says it keeps or states geometry."""
        from angelo_agent.action_catalog import CREATES, _mutations
        writers = {kind for kind in OPERATION_SCHEMAS
                   if (set(_mutations[kind]) | set(CREATES.get(kind, ()))) & derived_geometry.GEOMETRY_FAMILIES}
        self.assertEqual(writers - derived_geometry.GEOMETRY_KEEPS, {
            "mesh_bevel", "mesh_delete", "mesh_extrude", "mesh_inset", "mesh_merge_by_distance", "mesh_recalculate_normals",
            "mesh_separate", "mesh_subdivide", "modifier_apply", "object_join", "object_convert", "operator_run",
            "curve_points", "mesh_bend"})
        self.assertLessEqual(derived_geometry.GEOMETRY_KEEPS, set(OPERATION_SCHEMAS))

    def test_keeping_operators_are_admitted_and_unknown_ones_count(self):
        listed = {name for name in derived_geometry._KEEPING_OPERATORS}
        self.assertLessEqual(listed, set(ALLOWED), "Every listed keeper is an admitted operator")
        for name in ("mesh.subdivide", "mesh.flip_normals", "mesh.sort_elements", "object.shape_key_make_basis",
                     "curve.handle_type_set", "lattice.make_regular", "transform.translate", "mesh.attribute_set"):
            with self.subTest(operator=name):
                self.assertFalse(derived_geometry.keeps_geometry(name))
        self.assertFalse(derived_geometry.keeps_geometry("mesh.some_future_operator"))

    def test_the_rule_costs_little_beside_the_derivation(self):
        operations = []
        for index in range(8):
            alias = "$b%d" % index
            operations += [op("create_primitive", None, primitive="CUBE", name="B%d" % index, alias=alias[1:], size=1,
                              location=[index, 0, 0]),
                           op("transform", alias, mode="SET", location=[index, 1, 0]),
                           op("mesh_extrude", alias, face_selector={"normal": [0, 0, 1]}, translation=[0, 0, 1.0]),
                           op("modifier_add", alias, type="BEVEL", name="Bv", settings={"width": .1})]
        canonical = program(operations)
        started = time.perf_counter()
        derived_geometry.unchecked_geometry(canonical["operations"], [])
        self.assertLess(time.perf_counter() - started, 0.05, "32 operations; measured about 0.65 ms")


if __name__ == "__main__":
    unittest.main()
