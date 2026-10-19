# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""property_set: allow-list, typed rejections, RNA checks, derived checks and discovery.

Dependency-free: schemas and cross-field rules run directly; the kernel, the
property predicate and the commit transfer run against small RNA stand-ins.
Real RNA definitions, staging, commit and Undo are covered by
test_blender_property_set.py in the compiled app.
"""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import action_properties as properties  # noqa: E402
from angelo_agent.action_catalog import affected_references, catalog, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.action_property import apply_property_set, check_rna  # noqa: E402
from angelo_agent.action_properties import PROPERTY_PATHS, PropertyPathError  # noqa: E402
from angelo_agent.action_schemas import CAPABILITIES  # noqa: E402
from angelo_agent.derived_checks import derive_checks
from angelo_agent.program_checks import required_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS, DEFERRED_TOOLS, lookup  # noqa: E402
from angelo_agent.program_commit import _transfer_settings  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.scene_spec import validate_scene  # noqa: E402
from angelo_agent.validation_properties import evaluate_property  # noqa: E402
from angelo_agent.validation_reader import Inconclusive  # noqa: E402
from angelo_agent.validation_spec import check_references, validate_check  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SCENE = "scn:" + "a" * 32 + ":1"
CAM = "scn:" + "a" * 32 + ":9"
REV = "b" * 64


def op(target, **values):
    return {"kind": "property_set", "target": target, "parameters": {"values": values}}


def code_of(callable_, *args):
    try:
        callable_(*args)
    except PropertyPathError as error:
        return error.code
    except ValueError:
        return "VALUE_ERROR"
    return None


class Prop:
    """A minimal RNA property definition."""

    def __init__(self, kind, *, readonly=False, length=0, low=-1e9, high=1e9, length_max=0, fixed="Object", flag=False):
        self.type, self.is_readonly, self.array_length = kind, readonly, length
        self.hard_min, self.hard_max, self.length_max, self.is_enum_flag = low, high, length_max, flag
        self.fixed_type = SimpleNamespace(identifier=fixed)
        self.identifier, self.is_runtime, self.is_hidden, self.subtype = None, False, False, "NONE"
        self.array_dimensions = (length, 0, 0)


class Holder:
    """An RNA struct stand-in; ``clamp`` and ``refuse`` model RNA setters."""

    def __init__(self, props, values, clamp=None, refuse=()):
        object.__setattr__(self, "bl_rna", SimpleNamespace(properties=props, identifier="Struct"))
        for name, prop in props.items():
            prop.identifier = name
        object.__setattr__(self, "_clamp", clamp or {})
        object.__setattr__(self, "_refuse", set(refuse))
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        if name in self._refuse:
            raise TypeError('enum "%s" not found in (...)' % value)
        if name in self._clamp:
            value = min(value, self._clamp[name])
        object.__setattr__(self, name, value)


def object_holder(**overrides):
    props = {"hide_render": Prop("BOOLEAN"), "pass_index": Prop("INT", low=0, high=32767),
             "color": Prop("FLOAT", length=4, low=0, high=1e30), "display_type": Prop("ENUM")}
    values = {"hide_render": False, "pass_index": 0, "color": [1.0, 1.0, 1.0, 1.0], "display_type": "SOLID",
              "type": "MESH", "name": "Cube"}
    holder = Holder(props, values, **overrides)
    return holder


class Context:
    def __init__(self, objects=None, scene=None):
        self.objects, self.aliases = dict(objects or {}), {}
        self.scene, self.scene_handles = scene, frozenset({SCENE}) if scene is not None else frozenset()

    def resolve(self, reference):
        if reference in self.scene_handles:
            raise ValueError("scene handle")
        return self.objects[reference]

    def require_private(self, block):
        return block


class AllowListTests(unittest.TestCase):
    def test_every_path_has_an_owner_kind_and_valid_schema(self):
        for path, spec in PROPERTY_PATHS.items():
            with self.subTest(path=path):
                self.assertIn(spec[0], (properties.OBJECT, properties.SCENE))
                self.assertIn(spec[2], properties.RNA_TYPES)
                owner = SimpleNamespace()
                holder = owner
                for parent in path.split(".")[:-1]:
                    setattr(holder, parent, SimpleNamespace())
                    holder = getattr(holder, parent)
                self.assertEqual(properties.holder_and_name(owner, path), (holder, path.split(".")[-1]))
        self.assertEqual(list(properties.ORDER), list(PROPERTY_PATHS))
        self.assertLess(properties.ORDER["view_settings.view_transform"], properties.ORDER["view_settings.look"])
        self.assertLess(properties.ORDER["unit_settings.system"], properties.ORDER["unit_settings.length_unit"])
        self.assertEqual(properties.data_family("data.dof.use_dof"), "data")
        self.assertEqual(properties.data_family("active_material.roughness"), "material")
        self.assertEqual(properties.data_family("world.mist_settings.start"), "world")
        self.assertIsNone(properties.data_family("hide_render"))
        self.assertIsNone(properties.data_family("render.fps"))

    def test_no_path_reaches_files_scripts_drivers_or_custom_data(self):
        for path in PROPERTY_PATHS:
            lowered = path.lower()
            for word in ("filepath", "directory", "driver", "expression", "script", "[", "animation_data"):
                self.assertNotIn(word, lowered)
        # Datablock pointers: only the scene's active camera, typed CAMERA.
        pointers = [path for path in PROPERTY_PATHS if properties.value_kind(path) == "object"]
        self.assertEqual(pointers, ["camera"])
        self.assertEqual(PROPERTY_PATHS["camera"][3], "CAMERA")


class ValidationTests(unittest.TestCase):
    def test_accepts_and_canonicalizes_in_allow_list_order(self):
        result = validate_operation(op(OBJ, pass_index=3, hide_render=True, color=[1, 0, 0, 1]))
        self.assertEqual(list(result["parameters"]["values"]), ["hide_render", "color", "pass_index"])
        self.assertEqual(result["parameters"]["values"]["color"], [1, 0, 0, 1])  # RNA, not the schema, types it.
        generic = validate_operation(op(OBJ, **{"data.dof.aperture_ratio": 2, "show_shadows": True}))
        self.assertEqual(list(generic["parameters"]["values"]), ["data.dof.aperture_ratio", "show_shadows"])
        scene = validate_operation(op(SCENE, **{"view_settings.look": "Medium Contrast", "view_settings.view_transform": "AgX",
                                                "camera": CAM, "frame_end": 99, "frame_start": 2}))
        self.assertEqual(list(scene["parameters"]["values"])[:2], ["view_settings.view_transform", "view_settings.look"])
        self.assertEqual(affected_references(scene), (SCENE, CAM))
        self.assertEqual(validate_operation(op(SCENE, camera=None))["parameters"]["values"], {"camera": None})

    def test_typed_rejections(self):
        cases = {"render.filepath": "FILE_PATH", "render.image_settings.cache_directory": "FILE_PATH",
                 "animation_data.drivers": "SCRIPT_OR_DRIVER", "data.dof.driver": "SCRIPT_OR_DRIVER",
                 '["custom"]': "CUSTOM_PROPERTY_OR_INDEX", "modifiers[0].levels": "CUSTOM_PROPERTY_OR_INDEX",
                 "parent": "ID_POINTER", "world": "ID_POINTER", "data": "ID_POINTER", "active_material": "ID_POINTER",
                 "data.texture.intensity": "ID_POINTER", "location": "NOT_ALLOWED", "rotation_mode": "NOT_ALLOWED",
                 "Render.Engine": "NOT_ALLOWED", "__class__.__init__": "NOT_ALLOWED", "bl_rna.name": "NOT_ALLOWED",
                 "data.name": "NOT_ALLOWED", "tool_settings.use_snap": "NOT_ALLOWED", "a.b.c.d.e.f": "NOT_ALLOWED"}
        for path, code in cases.items():
            with self.subTest(path=path):
                self.assertEqual(code_of(validate_operation, op(OBJ, **{path: 1})), code)
        self.assertEqual(code_of(validate_operation, op(OBJ)), "EMPTY")
        self.assertEqual(code_of(validate_operation, op(OBJ, hide_render=True, **{"render.fps": 30})), "MIXED_OWNERS")
        self.assertIsNone(code_of(validate_operation, op(OBJ, **{"data.dof.focus_object": {"object": CAM}})))
        self.assertEqual(code_of(validate_operation, op(OBJ, **{"data.size": {"nested": 1}})), "RNA_TYPE_MISMATCH")
        self.assertEqual(code_of(validate_operation, op(SCENE, frame_start=20, frame_end=10)), "NOT_ALLOWED")
        self.assertEqual(code_of(validate_operation, op(SCENE, camera=SCENE)), "VALUE_ERROR")

    def test_schema_types_and_bounds_are_enforced(self):
        for values in ({"hide_render": 1}, {"pass_index": 40000}, {"pass_index": 1.5}, {"color": [1, 0, 0]},
                       {"color": [2, 0, 0, 1]}, {"display_type": "MAGIC"}, {"render.resolution_x": 99999},
                       {"render.engine": "BLENDER_EEVEE_NEXT"}, {"view_settings.look": "x" * 65},
                       {"view_settings.look": "bad\nlook"}, {"data.body": ""}, {"data.body": "x" * 1025},
                       {"camera": "not a handle!"}, {"render.fps": float("nan")}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_operation(op(SCENE, **values))
        self.assertEqual(len(dict.fromkeys(validate_operation(op(OBJ, **{f"show_{name}": True for name in ("wire", "name", "axis", "bounds", "in_front")}))["parameters"]["values"])), 5)
        too_many = {"show_setting_%d" % index: True for index in range(properties.MAX_VALUES + 1)}
        self.assertGreater(len(too_many), properties.MAX_VALUES)
        self.assertEqual(code_of(validate_operation, op(OBJ, **too_many)), "NOT_ALLOWED")

    def test_program_binding_rules(self):
        program = {"mode": "APPLY", "read_set": [], "write_set": [{"handle": SCENE, "revision": REV}],
                   "operations": [op(SCENE, camera=CAM)]}
        with self.assertRaises(ValueError):
            validate_program(program)  # The camera is a dependency: declare it.
        program["read_set"] = [{"handle": CAM, "revision": REV}]
        self.assertEqual(validate_program(program)["operations"][0]["parameters"]["values"]["camera"], CAM)
        with self.assertRaises(ValueError):
            validate_program(dict(program, write_set=[], read_set=[{"handle": SCENE, "revision": REV},
                                                                   {"handle": CAM, "revision": REV}]))

    def test_resource_families_are_narrowed_per_owner(self):
        self.assertEqual(resource_dependencies(op(SCENE, **{"render.fps": 30}))["mutates"], ["scene"])
        self.assertEqual(resource_dependencies(op(OBJ, hide_render=True))["mutates"], ["object", "properties"])
        self.assertEqual(resource_dependencies(op(OBJ, **{"data.dof.use_dof": True}))["mutates"], ["object", "data"])
        self.assertEqual(resource_dependencies(op(OBJ, **{"data.extrude": .1, "hide_viewport": True}))["mutates"],
                         ["object", "properties", "data"])
        self.assertEqual(resource_dependencies(op(OBJ, **{"active_material.roughness": .2}))["mutates"],
                         ["object", "properties"])
        self.assertEqual(resource_dependencies(op(SCENE, **{"world.mist_settings.start": 2}))["mutates"], ["scene"])
        self.assertEqual(resource_dependencies(op(OBJ, pass_index=1, hide_render=True))["properties"], ["hide_render", "pass_index"])


class DerivedCheckTests(unittest.TestCase):
    def canonical(self, *operations):
        return [validate_operation(operation) for operation in operations]

    def test_property_check_is_derived_merged_and_keeps_others(self):
        operations = self.canonical(op(OBJ, hide_render=True),
                                    {"kind": "transform", "target": OBJ, "parameters": {"location": [1, 2, 3]}},
                                    op(OBJ, hide_render=False, pass_index=4), op(SCENE, **{"render.fps": 25}))
        derived = derive_checks(operations)
        by_kind = {(check["kind"], check["target"]): check for check in derived}
        self.assertEqual(by_kind[("property", OBJ)]["values"], {"hide_render": False, "pass_index": 4})
        self.assertIn(("transform", OBJ), by_kind)
        self.assertEqual(by_kind[("property", SCENE)]["values"], {"render.fps": 25})
        program = {"operations": operations, "expected_checks": []}
        merged = required_checks(program)
        self.assertEqual(required_checks(program), merged)
        for check in merged:
            self.assertEqual(validate_check(check), check)

    def test_declared_property_check_never_suppresses_other_written_values(self):
        operations = self.canonical(op(OBJ, hide_render=True))
        declared = [validate_check({"kind": "property", "target": OBJ, "values": {"pass_index": 0}})]
        self.assertEqual(required_checks({"operations": operations, "expected_checks": declared}),
                         declared + [{"kind": "property", "target": OBJ, "values": {"hide_render": True}}])

    def test_check_values_are_strict_and_typed(self):
        self.assertEqual(code_of(validate_check, {"kind": "property", "target": OBJ, "values": {"render.filepath": "x"}}), "FILE_PATH")
        self.assertEqual(code_of(validate_check, {"kind": "property", "target": OBJ, "values": {"pass_index": -1}}), "VALUE_ERROR")
        self.assertEqual(code_of(validate_check, {"kind": "property", "target": OBJ, "values": {}}), "EMPTY")
        self.assertEqual(code_of(validate_check, {"kind": "property", "target": OBJ,
                                                  "values": {"hide_render": True, "render.fps": 2}}), "MIXED_OWNERS")
        check = validate_check({"kind": "property", "target": SCENE, "values": {"camera": "$cam", "render.fps": 30}})
        self.assertEqual(check_references(check), (SCENE, "$cam"))


class KernelTests(unittest.TestCase):
    def test_rna_definition_checks(self):
        cases = [(Prop("INT"), "hide_render", True, "RNA_TYPE_MISMATCH"),
                 (Prop("BOOLEAN", readonly=True), "hide_render", True, "RNA_READONLY"),
                 (Prop("FLOAT", length=3), "color", [1, 1, 1, 1], "RNA_ARRAY_LENGTH"),
                 (Prop("INT", length=2), "pass_index", 3, "RNA_ARRAY_LENGTH"),
                 (Prop("INT", low=0, high=10), "pass_index", 11, "RNA_RANGE"),
                 (Prop("ENUM", flag=True), "display_type", "WIRE", "RNA_TYPE_MISMATCH"),
                 (Prop("STRING", length_max=4), "data.body", "abcd", "STRING_TOO_LONG"),
                 (Prop("POINTER", fixed="Material"), "camera", CAM, "ID_POINTER"),
                 (Prop("STRING"), "data.body", 3, "RNA_TYPE_MISMATCH"),
                 (Prop("COLLECTION"), "data.splines", "x", "NOT_A_SETTING")]
        runtime = Prop("INT")
        runtime.is_runtime = True
        cases.append((runtime, "pass_index", 1, "RUNTIME_PROPERTY"))
        path_prop = Prop("STRING")
        path_prop.subtype = "FILE_PATH"
        cases.append((path_prop, "data.body", "x", "FILE_PATH"))
        for prop, path, value, code in cases:
            with self.subTest(code=code, path=path):
                name = path.split(".")[-1]
                holder = Holder({name: prop}, {})
                self.assertEqual(code_of(check_rna, holder, name, path, value), code)
        self.assertEqual(code_of(check_rna, Holder({}, {}), "hide_render", "hide_render", True), "PROPERTY_MISSING_IN_BUILD")

    def test_apply_reports_before_after_and_refuses_unretained_values(self):
        cube = object_holder()
        context = Context({OBJ: cube})
        result = apply_property_set(context, validate_operation(op(OBJ, hide_render=True, pass_index=5)))
        self.assertEqual(result["changes"], [{"path": "hide_render", "before": False, "after": True},
                                             {"path": "pass_index", "before": 0, "after": 5}])
        clamped = Context({OBJ: object_holder(clamp={"pass_index": 3})})
        self.assertEqual(code_of(apply_property_set, clamped, validate_operation(op(OBJ, pass_index=5))), "VALUE_NOT_RETAINED")
        refused = Context({OBJ: object_holder(refuse={"display_type"})})
        self.assertEqual(code_of(apply_property_set, refused, validate_operation(op(OBJ, display_type="WIRE"))),
                         "ENUM_ITEM_UNAVAILABLE")

    def test_owner_rules(self):
        scene = Holder({"fps": Prop("INT", low=1, high=1000)}, {"fps": 24})
        scene_owner = SimpleNamespace(render=scene)
        context = Context({OBJ: object_holder()}, scene=scene_owner)
        result = apply_property_set(context, validate_operation(op(SCENE, **{"render.fps": 30})))
        self.assertEqual((result["owner"], scene.fps), ("SCENE", 30))
        self.assertEqual(code_of(apply_property_set, context, validate_operation(op(OBJ, **{"render.fps": 30}))),
                         "OWNER_TYPE_MISMATCH")
        self.assertEqual(code_of(apply_property_set, context, validate_operation(op(SCENE, hide_render=True))),
                         "OWNER_TYPE_MISMATCH")
        self.assertEqual(code_of(apply_property_set, context, validate_operation(op(OBJ, **{"data.body": "Hi"}))),
                         "PROPERTY_MISSING_IN_BUILD")  # A mesh stand-in has no text data.

    def test_active_camera_must_be_a_camera(self):
        camera_prop = Prop("POINTER")
        scene_owner = Holder({"camera": camera_prop}, {"camera": None})
        lamp = SimpleNamespace(type="LIGHT")
        context = Context({CAM: lamp}, scene=scene_owner)
        self.assertEqual(code_of(apply_property_set, context, validate_operation(op(SCENE, camera=CAM))), "OBJECT_TYPE_MISMATCH")
        lens = SimpleNamespace(type="CAMERA")
        context = Context({CAM: lens}, scene=scene_owner)
        result = apply_property_set(context, validate_operation(op(SCENE, camera=CAM)))
        self.assertEqual(result["changes"], [{"path": "camera", "before": None, "after": CAM}])


class PredicateAndCommitTests(unittest.TestCase):
    def reader(self, targets):
        return SimpleNamespace(context=SimpleNamespace(targets=targets, resolve_target=targets.get),
                               spend=lambda amount: None)

    def test_predicate_passes_fails_and_refuses_animated_settings(self):
        cube = SimpleNamespace(type="MESH", hide_render=True, pass_index=4, color=(1., 0., 0., 1.), animation_data=None,
                               bl_rna=SimpleNamespace(identifier="Object"))
        check = validate_check({"kind": "property", "target": OBJ, "values": {"hide_render": True, "color": [1, 0, 0, 1]}})
        self.assertTrue(evaluate_property(self.reader({OBJ: cube}), cube, check)[0])
        passed, observed = evaluate_property(self.reader({OBJ: cube}), cube,
                                             validate_check({"kind": "property", "target": OBJ, "values": {"pass_index": 5}}))
        self.assertFalse(passed)
        self.assertEqual(observed["mismatched"], ["pass_index"])
        curve = SimpleNamespace(data_path="pass_index")
        layer = SimpleNamespace(strips=[SimpleNamespace(channelbags=[SimpleNamespace(fcurves=[curve])])])
        cube.animation_data = SimpleNamespace(drivers=[], action=SimpleNamespace(layers=[layer]), nla_tracks=[])
        with self.assertRaises(Inconclusive):
            evaluate_property(self.reader({OBJ: cube}), cube,
                              validate_check({"kind": "property", "target": OBJ, "values": {"pass_index": 4}}))
        scene_check = validate_check({"kind": "property", "target": SCENE, "values": {"render.fps": 24}})
        self.assertFalse(evaluate_property(self.reader({OBJ: cube}), cube, scene_check)[0])

    def test_commit_transfer_journals_and_verifies(self):
        live, staged, journal = object_holder(), object_holder(), []
        staged.hide_render, staged.pass_index = True, 9
        _transfer_settings(live, staged, ["hide_render", "pass_index"], {}, journal)
        self.assertEqual((live.hide_render, live.pass_index), (True, 9))
        self.assertEqual([(name, value) for _, name, value in journal], [("hide_render", False), ("pass_index", 0)])
        clamped, journal = object_holder(clamp={"pass_index": 2}), []
        with self.assertRaises(ProgramError):
            _transfer_settings(clamped, staged, ["pass_index"], {}, journal)
        self.assertEqual(len(journal), 1)  # The caller's rollback restores it.


class DiscoveryTests(unittest.TestCase):
    def test_new_kind_check_and_read_tool_are_deferred(self):
        self.assertIn("property_set", DEFERRED_ACTIONS)
        self.assertIn("property", DEFERRED_CHECKS)
        self.assertIn("angelo_scene_settings", DEFERRED_TOOLS)
        exact = lookup("ACTIONS", ["property_set"])["actions"][0]
        self.assertEqual(exact["kind"], "property_set")
        self.assertIn("propertyNames", str(exact["parameters"]))
        self.assertIn("camera", exact["parameters"]["properties"]["values"]["properties"])
        self.assertIn("scene_binding", CAPABILITIES["property_set"])
        self.assertEqual(catalog("property_set")["mutates"], ["object", "properties", "data", "scene"])
        self.assertEqual(resource_dependencies(op(OBJ, **{"data.temperature": 4000.0}))["mutates"], ["object", "data"])
        self.assertEqual(lookup("CHECKS", ["property"])["checks"][0]["properties"]["kind"]["const"], "property")
        self.assertEqual(validate_scene("angelo_scene_settings", {}), {})
        with self.assertRaises(ValueError):
            validate_scene("angelo_scene_settings", {"object": OBJ})

    def test_operations_are_never_mutated_by_validation(self):
        original = op(OBJ, hide_render=True)
        snapshot = copy.deepcopy(original)
        validate_operation(original)
        self.assertEqual(original, snapshot)


if __name__ == "__main__":
    unittest.main()
