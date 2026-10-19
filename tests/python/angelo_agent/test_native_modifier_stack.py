# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""New modifier types and stack edits: schemas, operands, work bounds, derived checks, commit sync.

Dependency-free. Real modifiers, evaluation, apply, rollback and Undo are
covered by test_blender_modifier_stack.py in the compiled app.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import affected_references, estimated_work, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.action_schemas import MODIFIER_SETTINGS  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.program_commit import _restore_stacks, _sync_stack, active_state, restore_active_state  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.refusal_codes import RefusalError, refusal_code  # noqa: E402
from angelo_agent.scene_read_fields import MODIFIER_FIELDS, REVISION_MODIFIERS  # noqa: E402
from angelo_agent.validation_spec import MODIFIER_PROPERTIES, validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":8"
NEW_TYPES = ("ARRAY", "DECIMATE", "WEIGHTED_NORMAL", "TRIANGULATE", "SHRINKWRAP", "DISPLACE", "SCREW", "CURVE")


def add(kind, **settings):
    return {"kind": "modifier_add", "target": OBJ, "parameters": {"type": kind, "settings": settings}}


def stack_op(kind, **parameters):
    return {"kind": kind, "target": OBJ, "parameters": parameters}


class Modifier:
    def __init__(self, name, kind, **settings):
        self.name, self.type, self.settings = name, kind, settings
        self.bl_rna = SimpleNamespace(properties=[])

    def keys(self):
        raise TypeError("this type doesn't support IDProperties")


class Stack(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def remove(self, item):
        list.remove(self, item)

    def move(self, source, destination):
        self.insert(destination, self.pop(source))

    def new(self, name, kind):
        self.append(Modifier(name, kind))
        return self[-1]


class SchemaTests(unittest.TestCase):
    def test_every_new_type_is_creatable_verifiable_and_revision_exact(self):
        for kind in NEW_TYPES:
            with self.subTest(kind=kind):
                self.assertIn(kind, MODIFIER_SETTINGS)
                self.assertIn(kind, MODIFIER_PROPERTIES)
                self.assertIn(kind, REVISION_MODIFIERS)
                self.assertTrue(set(MODIFIER_PROPERTIES[kind]) - {"show_viewport", "show_render"} <= set(MODIFIER_FIELDS[kind]))
        self.assertEqual(validate_operation(add("ARRAY", count=4))["parameters"]["settings"]["count"], 4)
        # A setting left out is not filled: the new modifier keeps Blender's own (action_schemas.SETTINGS_NOTE).
        self.assertEqual(validate_operation(add("SCREW"))["parameters"]["settings"], {})

    def test_operands_are_declared_dependencies_and_never_the_target(self):
        self.assertEqual(affected_references(add("SHRINKWRAP", target=OTHER)), (OBJ, OTHER))
        self.assertEqual(affected_references(add("CURVE", object=OTHER)), (OBJ, OTHER))
        for operation in (add("SHRINKWRAP"), add("CURVE"), add("SHRINKWRAP", target=OBJ), add("CURVE", object=OBJ),
                          add("ARRAY", count=65), add("ARRAY", fit_type="FIT_CURVE"), add("DISPLACE", texture="Clouds"),
                          add("SCREW", steps=1000), add("DECIMATE", ratio=0), add("skin"),
                          add("REMESH", octree_depth=9), add("REMESH", mode="QUAD")):
            with self.subTest(operation=operation["parameters"]), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_work_bounds_multiply_by_copies(self):
        shape = {"vertices": 8, "edges": 12, "faces": 6}
        base = estimated_work(add("DECIMATE"), shape)["evaluated_vertices"]
        self.assertEqual(estimated_work(add("ARRAY", count=10), shape)["evaluated_vertices"], base * 10)
        self.assertEqual(estimated_work(add("SCREW", steps=8, render_steps=4, iterations=2), shape)["evaluated_vertices"],
                         base * 9 * 2)

    def test_stack_kinds_and_families(self):
        self.assertEqual(resource_dependencies(stack_op("modifier_remove", modifier="Bevel"))["mutates"], ["object", "modifiers"])
        self.assertEqual(resource_dependencies(stack_op("modifier_apply", modifier="Bevel"))["mutates"], ["object", "mesh", "modifiers"])
        for operation in (stack_op("modifier_move", modifier="Bevel"), stack_op("modifier_move", modifier="Bevel", index=32),
                          stack_op("modifier_remove"), stack_op("modifier_apply", modifier="")):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)


class DerivedCheckTests(unittest.TestCase):
    def test_new_types_derive_settings_checks(self):
        checks = derive_checks([validate_operation(add("ARRAY", count=4)), validate_operation(add("SHRINKWRAP", target=OTHER))])
        array, wrap = checks
        self.assertEqual(array["values"]["count"], 4)
        self.assertNotIn("target", wrap["values"])
        for check in checks:
            self.assertEqual(validate_check(check), check)

    def test_stack_checks_merge_absent_names_and_place_moves(self):
        operations = [validate_operation(operation) for operation in (
            stack_op("modifier_remove", modifier="Bevel"), stack_op("modifier_apply", modifier="Subdivision"),
            stack_op("modifier_move", modifier="Mirror", index=0))]
        self.assertEqual(derive_checks(operations), [{"kind": "modifier_stack", "target": OBJ, "absent": ["Bevel", "Subdivision"],
                                                      "positions": [{"name": "Mirror", "index": 0}]}])
        # A later stack edit invalidates a placed position; an added modifier invalidates absence.
        moved_then_removed = [validate_operation(stack_op("modifier_move", modifier="Mirror", index=0)),
                              validate_operation(stack_op("modifier_remove", modifier="Bevel"))]
        self.assertEqual(derive_checks(moved_then_removed), [{"kind": "modifier_stack", "target": OBJ, "absent": ["Bevel"]}])
        removed_then_added = [validate_operation(stack_op("modifier_remove", modifier="Bevel")), validate_operation(add("ARRAY"))]
        self.assertEqual([check["kind"] for check in derive_checks(removed_then_added)], ["modifier"])
        settings_then_removed = [validate_operation(add("ARRAY")), validate_operation(stack_op("modifier_remove", modifier="Angelo ARRAY"))]
        self.assertEqual([check["kind"] for check in derive_checks(settings_then_removed)], ["modifier_stack"])

    def test_stack_check_validation(self):
        for check in ({"kind": "modifier_stack", "target": OBJ},
                      {"kind": "modifier_stack", "target": OBJ, "absent": ["A"], "positions": [{"name": "A", "index": 0}]},
                      {"kind": "modifier_stack", "target": OBJ, "positions": [{"name": "A", "index": 0}, {"name": "A", "index": 1}]}):
            with self.subTest(check=check), self.assertRaises(ValueError):
                validate_check(check)


class CommitSyncTests(unittest.TestCase):
    def test_sync_removes_orders_and_rolls_back(self):
        original = SimpleNamespace(modifiers=Stack([Modifier("A", "BEVEL"), Modifier("B", "ARRAY"), Modifier("C", "MIRROR")]))
        staged = SimpleNamespace(modifiers=Stack([Modifier("C", "MIRROR"), Modifier("A", "BEVEL")]))
        journal = []
        _sync_stack(original, staged, journal)
        self.assertEqual([item.name for item in original.modifiers], ["C", "A"])
        _restore_stacks(journal)
        self.assertEqual([(item.name, item.type) for item in original.modifiers], [("A", "BEVEL"), ("B", "ARRAY"), ("C", "MIRROR")])

    def test_active_modifier_and_track_are_restored_by_position_and_name(self):
        # modifiers.new and nla_tracks.new activate what they add, a rollback's re-created items too.
        class Tracks(list):
            @property
            def active(self):
                return next((item for item in self if item.active), None)

            @active.setter
            def active(self, track):
                for item in self:
                    item.active = item is track

        def track(name, active=False):
            return SimpleNamespace(name=name, active=active)

        modifiers = Stack([Modifier("A", "BEVEL"), Modifier("B", "ARRAY")])
        modifiers.active = modifiers[0]
        obj = SimpleNamespace(modifiers=modifiers, animation_data=SimpleNamespace(
            nla_tracks=Tracks([track("Base"), track("Pushed", True)])))
        state = active_state(obj)
        self.assertEqual(state, ("A", (1, "Pushed")))
        obj.modifiers.active = obj.modifiers.new("D", "SOLIDIFY")
        rebuilt = Tracks([track("Base"), track("Pushed"), track("New", True)])  # Rebuilt RNA, not the old pointers.
        obj.animation_data.nla_tracks = rebuilt
        restore_active_state(obj, state)
        self.assertIs(obj.modifiers.active, obj.modifiers.get("A"))
        self.assertEqual([item.active for item in rebuilt], [False, True, False])
        renamed = Tracks([track("Base"), track("Other", True)])
        obj.animation_data.nla_tracks = renamed
        restore_active_state(obj, state)  # Same position, another name: never activate the wrong track.
        self.assertEqual([item.active for item in renamed], [False, False])
        none = SimpleNamespace(modifiers=Stack([Modifier("A", "BEVEL")]), animation_data=None)
        none.modifiers.active = None
        self.assertEqual(active_state(none), (None, None))
        restore_active_state(none, (None, None))
        self.assertIsNone(none.modifiers.active)

    def test_sync_refuses_a_stack_it_cannot_match(self):
        original = SimpleNamespace(modifiers=Stack([Modifier("A", "BEVEL")]))
        staged = SimpleNamespace(modifiers=Stack([Modifier("A", "BEVEL"), Modifier("Z", "ARRAY")]))
        with self.assertRaises((ProgramError, ValueError)):
            _sync_stack(original, staged, [])


class ModifierRefusalTests(unittest.TestCase):
    """Run the production guards without loading Blender; unrelated native calls stay stubbed."""

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent/action_modifiers.py'
        spec = importlib.util.spec_from_file_location('angelo_agent._modifier_refusal_tests', path)
        cls.kernels = importlib.util.module_from_spec(spec)
        stubs = {
            'angelo_agent.action_budget': SimpleNamespace(_modifier_bound=lambda *args, **kwargs: 0,
                render_factor=Mock(side_effect=AssertionError("Render evaluation is outside these lookup-refusal tests"))),
            'angelo_agent.action_context': SimpleNamespace(_mesh=lambda *args, **kwargs: None,
                                                          _readback=lambda *args, **kwargs: kwargs,
                                                          _require_subdivision=lambda *args: None),
        }
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(cls.kernels)

    def test_typed_lookup_correction_keeps_candidate_names(self):
        obj = SimpleNamespace(modifiers=Stack([Modifier('Bevel', 'BEVEL')]))
        operation = {'kind': 'modifier_configure', 'parameters': {'type': 'BEVEL', 'modifier': 'Angelo BEVEL'}}
        with self.assertRaises(RefusalError) as raised:
            self.kernels._modifier(None, obj, operation)
        self.assertEqual(refusal_code(raised.exception), 'VALIDATION')
        self.assertIn("its BEVEL modifiers: 'Bevel'", str(raised.exception))

    def test_generic_lookup_correction_preserves_value_error_compatibility(self):
        obj = SimpleNamespace(modifiers=Stack([]))
        operation = {'kind': 'modifier_configure', 'parameters': {'type': 'SKIN', 'modifier': 'Skin'}}
        with self.assertRaises(ValueError) as raised:
            self.kernels._generic_modifier(None, obj, operation)
        self.assertEqual(refusal_code(raised.exception), 'VALIDATION')

    def test_stack_lookup_refuses_missing_names_and_returns_existing_modifier(self):
        modifier = Modifier('Bevel', 'BEVEL')
        obj = SimpleNamespace(modifiers=Stack([modifier]))
        self.assertIs(self.kernels._named(obj, 'Bevel'), modifier)
        with self.assertRaises(RefusalError) as raised:
            self.kernels._named(obj, 'Missing')
        self.assertEqual(refusal_code(raised.exception), 'VALIDATION')

    def test_lookup_failure_text_alone_never_grants_correction_authority(self):
        message = 'Choose an existing staged modifier of the exact declared type'

        class BrokenStack(Stack):
            def get(self, name):
                raise ValueError(message)

        operation = {'kind': 'modifier_configure', 'parameters': {'type': 'BEVEL', 'modifier': 'Bevel'}}
        with self.assertRaises(ValueError) as raised:
            self.kernels._modifier(None, SimpleNamespace(modifiers=BrokenStack()), operation)
        self.assertEqual(refusal_code(raised.exception), 'UNKNOWN')

    def test_silent_setting_failure_never_grants_correction_authority(self):
        with self.assertRaises(ValueError) as raised:
            self.kernels._verify_settings(SimpleNamespace(width=1.), {'width': 2.})
        self.assertEqual(refusal_code(raised.exception), 'INTERNAL_ERROR')

    def test_arbitrary_native_creation_error_keeps_unknown_authority(self):
        class BrokenStack(Stack):
            def new(self, name, kind):
                raise RuntimeError('The exact object revision changed')

        operation = {'kind': 'modifier_add', 'parameters': {'type': 'SKIN', 'name': 'Skin', 'settings': {}}}
        with self.assertRaises(RefusalError) as raised:
            self.kernels._generic_modifier(None, SimpleNamespace(modifiers=BrokenStack()), operation)
        self.assertEqual(refusal_code(raised.exception), 'UNKNOWN')
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
