# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run's phase-5 families without Blender: tables, pure refusals, families, kernel rules, state and commit.

Dependency-free; the kernel is imported against a stand-in ``bpy`` only
inside a test. The same rules run on real operators in
test_blender_operator_run_families.py, test_blender_operator_run_objects.py
and, with a second window open, test_blender_operator_run_windows.py.
"""

import ast
import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_families as families, action_operator_spec as spec, operator_state  # noqa: E402
from angelo_agent.action_catalog import resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"


def op(**parameters):
    return {"kind": "operator_run", "target": OBJ, "parameters": parameters}


class TableTests(unittest.TestCase):
    def test_families_are_closed_and_consistent(self):
        self.assertTrue(set(families.ALLOWED) <= set(spec.ALLOWED))
        self.assertFalse(set(spec.ALLOWED) & set(spec.DENIED))
        self.assertTrue(set(families.DENIED) <= set(spec.DENIED))
        self.assertTrue(set(families.OPERATOR_FAMILIES) <= set(families.ALLOWED))
        self.assertTrue(set(families.OPERATOR_TARGETS) <= set(families.ALLOWED))
        self.assertTrue(families.EDIT_OPERATORS and all(name.startswith("grease_pencil.") for name in families.EDIT_OPERATORS))
        for name in list(families.ALLOWED) + list(families.DENIED):
            with self.subTest(name=name):
                self.assertRegex(name, spec.OPERATOR_PATTERN)
        for name in families.ALLOWED:
            with self.subTest(name=name):
                self.assertTrue(set(spec.target_types(name)) <= set(families.OWNER_TYPES) | set(families.EDIT_TYPES))
                self.assertTrue(set(spec.ALLOWED[name][1]) <= set(spec.CAPS))
        self.assertEqual(spec.target_types("transform.shrink_fatten"), ("MESH",))
        self.assertEqual(spec.target_types("transform.translate"), families.EDIT_TYPES)
        self.assertEqual(spec.target_types("grease_pencil.stroke_smooth"), ("GREASEPENCIL",))
        self.assertEqual(spec.target_types("constraint.delete"), families.OWNER_TYPES)
        self.assertEqual(spec.target_types("object.origin_clear"), families.OWNER_TYPES)
        self.assertEqual(spec.target_types("object.skin_root_mark"), ("MESH",))
        # Measured bounds: superlinear extrusion of strokes, the recursion that overflowed the stack.
        self.assertEqual(spec.WORK_BOUNDS["grease_pencil.extrude"], families.WORK_BOUNDS["grease_pencil.extrude"])
        self.assertFalse(spec.within_work_bound("grease_pencil.extrude", 90000, 90000))
        self.assertTrue(spec.within_work_bound("grease_pencil.extrude", 90000, 100))
        self.assertTrue(set(families.RECURSION_BOUNDS) <= set(families.ALLOWED))
        # Measured growth: a Bezier or Catmull-Rom stroke or hair curve converted with its handles or kept as NURBS
        # triples its points; closing a stroke adds none (density matching, which is unbounded, stays off).
        self.assertEqual(spec.ALLOWED["grease_pencil.set_curve_type"], (3, {}))
        self.assertEqual(spec.ALLOWED["curves.curve_type_set"], (3, {}))
        self.assertIs(families.DEFAULTS["grease_pencil"]["subdivide_cyclic_segment"], False)
        for name in ("grease_pencil.select_similar", "grease_pencil.convert_curve_type",
                     "grease_pencil.stroke_merge_by_distance", "curves.attribute_set", "object.modifiers_clear"):
            self.assertIn(name, spec.DENIED, name)
            self.assertNotIn(name, spec.ALLOWED, name)

    def test_modes(self):
        for name in ("transform.translate", "curves.subdivide", "font.text_insert", "grease_pencil.stroke_smooth"):
            self.assertTrue(spec.edit_only(name), name)
        for name in ("grease_pencil.layer_add", "constraint.delete", "object.origin_clear", "object.skin_root_mark"):
            self.assertFalse(spec.edit_only(name), name)

    def test_transform_pins_never_fall_back_to_tool_settings(self):
        self.assertEqual(families.PINNED["transform"], {"snap": False, "use_automerge_and_split": False,
                                                        "use_snap_project": False})
        self.assertEqual(families.DEFAULTS["transform"]["orient_type"], "GLOBAL")
        self.assertIs(families.DEFAULTS["transform"]["use_proportional_edit"], False)
        self.assertEqual(families.HIDDEN_SETTABLE, {"transform": {"center_override"}, "constraint": {"constraint"}})


class PureRefusalTests(unittest.TestCase):
    def test_denied_names_and_values(self):
        cases = {
            "VIEW_STATE": [op(operator="transform.bend"), op(operator="transform.vertex_warp"),
                           op(operator="transform.shear"), op(operator="grease_pencil.reproject"),
                           op(operator="transform.translate", properties={"orient_type": "VIEW"}),
                           op(operator="transform.rotate", properties={"orient_type": "CURSOR"}),
                           op(operator="transform.resize", properties={"use_proportional_projected": True})],
            "CURSOR_PICK": [op(operator="transform.vert_slide"), op(operator="curves.draw"), op(operator="font.select_word")],
            "SCENE_STATE": [op(operator="transform.create_orientation")],
            "COVERED": [op(operator="transform.transform")],
            "NEW_OBJECTS": [op(operator="grease_pencil.separate"), op(operator="curves.separate")],
            "CLIPBOARD": [op(operator="grease_pencil.paste"), op(operator="font.text_paste")],
            "SHARED_DATA": [op(operator="grease_pencil.material_hide")],
            "OTHER_OBJECTS": [op(operator="curves.snap_curves_to_surface"), op(operator="object.vertex_parent_set"),
                              op(operator="constraint.copy_to_selected", properties={"constraint": "A"}),
                              op(operator="constraint.followpath_path_animate", properties={"constraint": "A"})],
            "MOTION_TRACKING": [op(operator="constraint.objectsolver_set_inverse", properties={"constraint": "A"})],
            "TEXT_CURSOR": [op(operator="font.move")], "FILE": [op(operator="font.open")],
            "INTERACTIVE": [op(operator="font.text_insert_unicode")],
            "MODIFIER_BIND_DATA": [op(operator="object.meshdeform_bind")],
            "FRAME_STEPPING": [op(operator="object.paths_calculate")],
            "UNBOUNDED_GROWTH": [op(operator="grease_pencil.stroke_simplify",
                                    properties={"mode": "SAMPLE", "length": 1e-5}),
                                 op(operator="grease_pencil.cyclical_set",
                                    properties={"subdivide_cyclic_segment": True}),
                                 op(operator="grease_pencil.convert_curve_type", properties={"type": "POLY"}),
                                 op(operator="curves.attribute_set", properties={"value_int": 1000000000})],
            "SUPERLINEAR": [op(operator="grease_pencil.stroke_simplify", properties={"mode": "ADAPTIVE"}),
                            op(operator="grease_pencil.select_similar")],
            "UPSTREAM_DEFECT": [op(operator="grease_pencil.stroke_simplify", properties={"mode": "MERGE"}),
                                op(operator="grease_pencil.stroke_merge_by_distance")],
            "COVERED": [op(operator="object.modifiers_clear")],
            "CONSTRAINT_NAME": [op(operator="constraint.delete"), op(operator="constraint.copy", properties={"index": 1})],
            "SELECTION": [op(operator="font.delete", select="KEEP"),
                          op(operator="constraint.delete", select="ALL", properties={"constraint": "A"}),
                          op(operator="curves.delete", select="INDICES", vertex_indices=[0])],
            "NOT_ALLOWED": [op(operator="curves.sculptmode_toggle"), op(operator="object.modifier_copy")],
        }
        for code, operations in cases.items():
            for operation in operations:
                with self.subTest(code=code, operator=operation["parameters"]["operator"]), \
                        self.assertRaisesRegex(ValueError, "operator_run " + code):
                    validate_operation(operation)

    def test_admitted_shapes(self):
        moved = validate_operation(op(operator="transform.translate", select="INDICES", face_indices=[0],
                                      properties={"value": [0, 0, 1], "center_override": [1, 0, 0]}))
        self.assertEqual(moved["parameters"]["properties"], {"value": [0, 0, 1], "center_override": [1, 0, 0]})
        named = validate_operation(op(operator="constraint.move_to_index", properties={"constraint": "Copy", "index": 0}))
        self.assertIs(type(named["parameters"]["properties"]["index"]), int)
        validate_operation(op(operator="font.text_insert", select="NONE", properties={"text": " QA"}))
        validate_operation(op(operator="grease_pencil.stroke_simplify", properties={"mode": "FIXED", "steps": 2}))
        validate_operation(op(operator="grease_pencil.cyclical_set", properties={"type": "CLOSE",
                                                                                "subdivide_cyclic_segment": False}))
        validate_operation(op(operator="transform.translate", properties={"orient_type": "NORMAL",
                                                                          "use_proportional_edit": True}))


class FamilyTests(unittest.TestCase):
    def test_commit_families(self):
        mutates = lambda name, **extra: resource_dependencies(validate_operation(op(operator=name, **extra)))["mutates"]
        self.assertEqual(mutates("constraint.delete", properties={"constraint": "A"}), ["object", "constraints"])
        self.assertEqual(mutates("constraint.apply", properties={"constraint": "A"}), ["object", "transforms", "constraints"])
        self.assertEqual(mutates("object.track_clear"), ["object", "transforms", "constraints"])
        self.assertEqual(mutates("object.origin_clear"), ["object", "transforms"])
        for name in ("transform.translate", "curves.subdivide", "font.line_break", "grease_pencil.delete",
                     "object.skin_root_mark"):
            self.assertEqual(mutates(name), ["object", "data"], name)

    def test_derived_checks_an_operator_could_falsify_are_dropped(self):
        added = validate_operation({"kind": "constraint_add", "target": OBJ, "parameters": {
            "type": "LIMIT_DISTANCE", "name": "Keep", "settings": {"distance": 2.0}}})
        self.assertEqual([check["kind"] for check in derive_checks([added])], ["constraint_values"])
        deleted = validate_operation(op(operator="constraint.delete", properties={"constraint": "Keep"}))
        self.assertEqual(derive_checks([added, deleted]), [])
        bone = validate_operation({"kind": "bone_set", "target": OBJ, "parameters": {"bone": "Root", "location": [1, 0, 0]}})
        self.assertTrue(derive_checks([bone]))
        self.assertEqual(derive_checks([bone, validate_operation(op(operator="pose.loc_clear"))]), [])
        modifier = validate_operation({"kind": "modifier_add", "target": OBJ, "parameters": {
            "type": "BEVEL", "name": "Edge", "settings": {"width": .1}}})
        self.assertTrue(derive_checks([modifier]))
        self.assertEqual(derive_checks([modifier, validate_operation(op(operator="mesh.subdivide"))]), [])


def _kernel():
    return importlib.import_module("angelo_agent.action_operators")


class KernelRuleTests(unittest.TestCase):
    """Admission and property rules of the families with a stand-in ``bpy``."""

    def setUp(self):
        package = sys.modules["angelo_agent"]
        before = set(vars(package))
        self.addCleanup(lambda: [delattr(package, name) for name in set(vars(package)) - before])

    @staticmethod
    def prop(identifier, kind, hidden=False, default=0):
        return SimpleNamespace(identifier=identifier, type=kind, subtype="NONE", is_hidden=hidden, is_readonly=False,
                               default=default, array_dimensions=(0, 0, 0), is_array=False)

    def stand_in(self, options):
        call = SimpleNamespace(idname=lambda: "TRANSFORM_OT_translate", bl_options=set(options))
        props = [self.prop("value", "FLOAT"), self.prop("orient_type", "ENUM"), self.prop("snap", "BOOLEAN", True),
                 self.prop("center_override", "FLOAT", True), self.prop("use_proportional_edit", "BOOLEAN")]
        rna = SimpleNamespace(properties=_Properties(props))
        call.get_rna_type = lambda: rna
        bpy = SimpleNamespace(ops=SimpleNamespace(transform=SimpleNamespace(translate=call)), context=SimpleNamespace(),
                              data=SimpleNamespace(), types=SimpleNamespace(Operator=SimpleNamespace(
                                  bl_rna_get_subclass_py=lambda name: None)))
        return bpy, rna

    def test_transforms_are_blocking_only_when_invoked_and_get_pins(self):
        bpy, rna = self.stand_in(("REGISTER", "UNDO", "BLOCKING"))
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}), \
                patch("angelo_agent.rna_rules.check_value", lambda prop, name, value: None):
            kernel = _kernel()
            call, found, growth = kernel.admission("transform.translate")
            keywords, _ = kernel.check_properties("transform.translate", found, growth,
                                                  {"value": 1.0, "center_override": [1, 0, 0]})
            self.assertEqual(keywords, {"value": 1.0, "center_override": [1, 0, 0], "snap": False,
                                        "orient_type": "GLOBAL", "use_proportional_edit": False})
            with self.assertRaisesRegex(spec.OperatorError, "PROPERTY_NOT_SETTABLE"):
                kernel.check_properties("transform.translate", found, growth, {"snap": True})
        bpy, rna = self.stand_in(("REGISTER", "UNDO", "BLOCKING", "DEPENDS_ON_CURSOR"))
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}):
            with self.assertRaisesRegex(spec.OperatorError, "INTERACTIVE"):
                _kernel().admission("transform.translate")


    def test_constraint_operators_refuse_a_stack_with_an_armature_constraint(self):
        class Stack(list):
            def get(self, name):
                return next((item for item in self if item.name == name), None)

        def owner(*kinds):
            constraints = Stack(SimpleNamespace(name=kind.title(), type=kind) for kind in kinds)
            return SimpleNamespace(type="MESH", data=None, animation_data=None, constraints=constraints, modifiers=[])

        bpy, _ = self.stand_in(("REGISTER", "UNDO"))
        modules = {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}
        with patch.dict(sys.modules, modules):
            kernel = _kernel()
            # Commit and its rollback rebuild constraints from RNA settings, which hold no target list.
            for idname, keywords in (("constraint.delete", {"constraint": "Limit_Location"}),
                                     ("constraint.move_up", {"constraint": "Limit_Location"}),
                                     ("object.track_clear", {})):
                with self.subTest(operator=idname), self.assertRaisesRegex(spec.OperatorError, "CONSTRAINT_TARGETS"):
                    kernel._guard_frees(idname, owner("LIMIT_LOCATION", "ARMATURE"), keywords)
                kernel._guard_frees(idname, owner("LIMIT_LOCATION", "TRACK_TO"), keywords)
            # Operators outside the constraints family leave such a stack alone.
            kernel._guard_frees("object.origin_clear", owner("ARMATURE"), {})


class _Properties(list):
    """RNA properties by identifier, as bpy_prop_collection looks them up."""

    def get(self, name):
        return next((prop for prop in self if prop.identifier == name), None)

    def __contains__(self, name):
        return self.get(name) is not None


class CurveCountTests(unittest.TestCase):
    """The curve_counts check: schema rules and the predicate on stand-ins of each type."""

    def test_schema(self):
        from angelo_agent.validation_spec import validate_checks
        count = {"minimum": 2, "maximum": 2}
        self.assertEqual(len(validate_checks([{"kind": "curve_counts", "target": OBJ, "points": count}])), 1)
        for bad in ({"kind": "curve_counts", "target": OBJ},
                    {"kind": "curve_counts", "target": OBJ, "lines": {"minimum": 3, "maximum": 2}},
                    {"kind": "curve_counts", "target": OBJ, "faces": count}):
            with self.subTest(check=bad), self.assertRaises(ValueError):
                validate_checks([bad])

    def test_predicate(self):
        from angelo_agent.validation_curves import evaluate_curve_counts
        reader = SimpleNamespace(spend=lambda amount: None)
        check = lambda **counts: {key: {"minimum": value, "maximum": value} for key, value in counts.items()}
        text = SimpleNamespace(type="FONT", data=SimpleNamespace(body="Angelo\nStudio"))
        self.assertTrue(evaluate_curve_counts(reader, text, check(characters=13, lines=2))[0])
        self.assertEqual(evaluate_curve_counts(reader, text, check(points=0))[1]["applies"], False)
        hair = SimpleNamespace(type="CURVES", data=SimpleNamespace(points=[0] * 21, curves=[0] * 3))
        self.assertTrue(evaluate_curve_counts(reader, hair, check(points=21, curves=3))[0])
        self.assertFalse(evaluate_curve_counts(reader, hair, check(points=20))[0])
        drawing = SimpleNamespace(attributes={"position": SimpleNamespace(data=[0] * 14)}, curve_offsets=[0, 7, 14])
        layer = SimpleNamespace(current_frame=lambda: SimpleNamespace(drawing=drawing))
        empty = SimpleNamespace(current_frame=lambda: None)
        sketch = SimpleNamespace(type="GREASEPENCIL", data=SimpleNamespace(layers=[layer, empty]))
        self.assertEqual(evaluate_curve_counts(reader, sketch, check(points=14, curves=2))[1]["points"], 14)
        mesh = SimpleNamespace(type="MESH", data=None)
        self.assertFalse(evaluate_curve_counts(reader, mesh, check(points=0))[0])


class StateTests(unittest.TestCase):
    def test_skin_state_reads_the_layer_in_bulk(self):
        class Data(list):
            def foreach_get(self, prop, values):
                width = len(values) // len(self)
                for index, item in enumerate(self):
                    values[index * width:(index + 1) * width] = type(values)(values.typecode, getattr(item, prop))

        vertex = SimpleNamespace(radius=(.5, .25), use_root=(1,), use_loose=(0,))
        mesh = SimpleNamespace(skin_vertices=[SimpleNamespace(data=Data([vertex, vertex]))])
        self.assertEqual(len(operator_state.skin_state(mesh)), 3)
        self.assertIsNone(operator_state.skin_state(SimpleNamespace(skin_vertices=[])))

    def test_parent_inverse_commits_with_a_journal(self):
        class Matrix(tuple):
            def copy(self):
                return Matrix(self)

        names = operator_state.OBJECT_INDEXES
        live = SimpleNamespace(**dict.fromkeys(names, 0), field=None, matrix_parent_inverse=Matrix((1, 0)))
        staged = SimpleNamespace(**dict.fromkeys(names, 0), field=None, matrix_parent_inverse=Matrix((0, 1)))
        journal = []
        operator_state.commit_object_state(live, staged, journal, RuntimeError)
        self.assertEqual((live.matrix_parent_inverse, journal), (Matrix((0, 1)), [(live, "matrix_parent_inverse", (1, 0))]))

    def test_constraint_stack_sync_by_name(self):
        class Stack(list):
            def get(self, name):
                return next((item for item in self if item.name == name), None)

            def new(self, kind):
                self.append(SimpleNamespace(name=kind.title(), type=kind))
                return self[-1]

            def move(self, source, target):
                self.insert(target, self.pop(source))

        item = lambda name, kind: SimpleNamespace(name=name, type=kind)
        live = SimpleNamespace(constraints=Stack([item("A", "LIMIT_LOCATION"), item("B", "TRACK_TO"), item("C", "CHILD_OF")]))
        staged = SimpleNamespace(constraints=Stack([item("C", "CHILD_OF"), item("A.001", "LIMIT_LOCATION"),
                                                    item("A", "LIMIT_LOCATION")]))
        applied, journal = [], []
        with patch("angelo_agent.program_transfers._settings", lambda value: {}):
            operator_state.sync_constraints(live, staged, {}, journal, lambda *args: applied.append(args[0].name),
                                            RuntimeError)
        self.assertEqual([(entry.name, entry.type) for entry in live.constraints],
                         [("C", "CHILD_OF"), ("A.001", "LIMIT_LOCATION"), ("A", "LIMIT_LOCATION")])
        self.assertEqual(applied, ["C", "A.001", "A"])
        self.assertEqual([record["name"] for record in journal[0][2]], ["A", "B", "C"])

    def test_commit_syncs_constraints_before_the_typed_kinds(self):
        source = (ADDON / "program_commit.py").read_text()
        self.assertLess(source.index("sync_constraints(original"), source.index('"kind:constraint_add" in resources'))
        restore = (ADDON / "program_transfers.py").read_text()
        self.assertIn("restore_constraints(obj, records)", restore)

    def test_unbounded_grease_pencil_and_hair_modifiers_are_refused_before_evaluation(self):
        package = sys.modules["angelo_agent"]
        before = set(vars(package))
        self.addCleanup(lambda: [delattr(package, name) for name in set(vars(package)) - before])
        bpy = SimpleNamespace(types=SimpleNamespace(), data=SimpleNamespace(), context=SimpleNamespace(), ops=SimpleNamespace())
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": SimpleNamespace(Vector=tuple), "bmesh": SimpleNamespace()}):
            budget = importlib.import_module("angelo_agent.action_budget")
            context = SimpleNamespace(assert_staged=lambda obj: obj, owned_ids=[])
            for kind in ("GREASEPENCIL", "CURVES"):
                with self.subTest(kind=kind):
                    self.assertEqual(budget.assert_evaluation_budget(context, SimpleNamespace(type=kind, modifiers=[])), 0)
                    with self.assertRaisesRegex(ValueError, "Grease Pencil or hair-curve modifiers"):
                        budget.assert_evaluation_budget(context, SimpleNamespace(type=kind, modifiers=["Array"]))

    def test_stage_scene_pins_auto_keying_off(self):
        source = (ADDON / "program_objects.py").read_text()
        create = source.split("def create_stage(self, binding):", 1)[1].split("stage = Stage(", 1)[0]
        self.assertIn("scene.tool_settings.use_keyframe_insert_auto = False", create)

    def test_modules_stay_bounded(self):
        for name in ("action_operator_spec.py", "action_operators.py", "action_operator_modes.py",
                     "action_operator_families.py", "operator_state.py"):
            with self.subTest(module=name):
                self.assertLessEqual(len((ADDON / name).read_text().splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
