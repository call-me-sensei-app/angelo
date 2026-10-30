# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent.execution_spec import EXECUTION_TOOLS, validate_execution
from angelo_agent.refusal_codes import refusal_code


class ExecutionSpecTests(unittest.TestCase):
    def test_closed_discovery_families(self):
        for family in ['SUMMARY','ACTIONS','QUERIES','CHECKS','RECIPES']:
            self.assertEqual(validate_execution('angelo_agent_capabilities', {'family':family}), {'family':family})
        for invalid in ['SHELL','AUTH','',None,True,[],{}]:
            with self.assertRaises(ValueError):
                validate_execution('angelo_agent_capabilities', {'family':invalid})

    def test_bounded_metrics_count(self):
        for count in [1,20,100]:
            self.assertEqual(validate_execution('angelo_agent_metrics', {'limit':count}), {'limit':count})
        for invalid in [True,0,101,1.0,'10',float('inf')]:
            with self.assertRaises(ValueError):
                validate_execution('angelo_agent_metrics', {'limit':invalid})

    def test_discovery_does_not_advertise_mutation(self):
        for tool in EXECUTION_TOOLS:
            self.assertTrue(tool['annotations']['readOnlyHint'])
            self.assertFalse(tool['annotations']['openWorldHint'])
            self.assertFalse(tool['inputSchema']['additionalProperties'])

    def test_api_lookup_has_no_expression_or_path_escape(self):
        value={'domain':'RNA','name':'Object','member':'modifiers','offset':0,'limit':32}
        self.assertEqual(validate_execution('angelo_blender_api',value),value)
        for bad in ['__dict__','Object.modifiers','/etc/passwd',"eval('x')",'Object[0]']:
            with self.assertRaises(ValueError):
                validate_execution('angelo_blender_api',dict(value,name=bad))
        with self.assertRaises(ValueError):
            validate_execution('angelo_blender_api',dict(value,offset=True))
        # An operator's bl_idname is the one dotted name: module.name, lower case, RNA names only.
        operator=dict(value,name='object.shade_smooth',member='')
        self.assertEqual(validate_execution('angelo_blender_api',operator),operator)
        for bad in ['object.shade_smooth.x','Object.shade_smooth','.shade','object.']:
            with self.assertRaises(ValueError):
                validate_execution('angelo_blender_api',dict(operator,name=bad))
        with self.assertRaises(ValueError):
            validate_execution('angelo_blender_api',dict(operator,domain='BMESH'))
        with self.assertRaises(ValueError):
            validate_execution('angelo_blender_api',dict(value,member='object.shade_smooth'))
        with self.assertRaises(ValueError):
            validate_execution('angelo_blender_api',dict(value,domain='BMESH'))


class ApiLookupTests(unittest.TestCase):
    """``bpy.types`` has no attribute for a type a registered add-on defines (measured on Blender 5.2.2;
    ``test_blender_api_lookup.py`` checks the real Cycles types)."""

    def setUp(self):
        from angelo_agent import api_lookup
        self.execute = api_lookup.execute
        samples = SimpleNamespace(identifier="samples", name="Samples", type="INT", description="Render samples",
                                  is_readonly=False, hard_min=1, hard_max=16777216, array_length=0)

        class bpy_struct:
            @classmethod
            def bl_rna_get_subclass_py(cls, name, default=None):
                return {"CyclesRenderSettings": CyclesRenderSettings}.get(name, default)

        class Object(bpy_struct):
            bl_rna = SimpleNamespace(identifier="Object", description="Object", properties=[], functions=[])

        # Blender's Python layer over an RNA type (_bpy_types), as bpy.types.Mesh inherits it.
        def from_pydata(self, vertices, edges, faces, shade_flat=False):
            """Make this mesh from lists of vertices, edges and faces."""
        Layer = type("Mesh", (bpy_struct,), {"__module__": "_bpy_types", "from_pydata": from_pydata,
                                              "edge_keys": property(lambda self: []), "_hidden": from_pydata})
        Mesh = type("Mesh", (Layer,), {"bl_rna": SimpleNamespace(identifier="Mesh", description="Mesh",
                                                                 properties=[samples], functions=[])})

        class CyclesRenderSettings(bpy_struct):
            bl_rna = SimpleNamespace(identifier="CyclesRenderSettings", description="Cycles", properties=[samples],
                                     functions=[])

        class Unregistered(CyclesRenderSettings):  # Inherits its parent's RNA: not a type of its own.
            pass

        # bpy.ops: operators by module, C or Python alike; get_rna_type returns the RNA without running anything.
        angle = SimpleNamespace(identifier="angle", name="Angle", type="FLOAT", description="Maximum angle",
                                is_readonly=False, hard_min=0.0, hard_max=3.14159, array_length=0)
        self.ran = []

        class Operator:
            def __init__(self, identifier, properties):
                self.rna = SimpleNamespace(identifier=identifier, description=identifier + " operator",
                                           properties=properties, functions=[])

            def get_rna_type(self):
                return self.rna

            def __call__(self, *args, **kwargs):
                self.ran.append(self.rna.identifier)

        Operator.ran = self.ran

        class Submodule:
            def __init__(self, **operators):
                self.__dict__.update(operators)

            def __dir__(self):
                return list(self.__dict__)

            def __getattr__(self, name):   # As bpy.ops: any attribute "exists" until get_rna_type is asked.
                return Operator(name.upper() + "_missing", [])

        class Ops:
            object = Submodule(smooth_by_angle=Operator("OBJECT_OT_smooth_by_angle", [angle]))
            mesh = Submodule(primitive_cube_add=Operator("MESH_OT_primitive_cube_add", [samples]))

            def __dir__(self):
                return ["mesh", "object"]

        fake = SimpleNamespace(types=SimpleNamespace(bpy_struct=bpy_struct, Object=Object, Mesh=Mesh), ops=Ops(),
                               app=SimpleNamespace(version_string="5.2.2 LTS"))
        patcher = patch.dict(sys.modules, {"bpy": fake, "bmesh": SimpleNamespace(ops=SimpleNamespace())})
        patcher.start()
        self.addCleanup(patcher.stop)

    def lookup(self, **arguments):
        return self.execute(dict({"domain": "RNA", "name": "", "member": "", "offset": 0, "limit": 64}, **arguments))

    def test_an_addon_settings_type_is_found_by_its_identifier(self):
        self.assertEqual(self.lookup(name="CyclesRenderSettings")["items"], [{"name": "samples", "kind": "property", "type": "INT"}])
        self.assertEqual(self.lookup(name="CyclesRenderSettings", member="samples")["property"]["hard_max"], 16777216)

    def test_the_type_directory_lists_registered_addon_types_once(self):
        self.assertEqual(self.lookup()["items"], ["CyclesRenderSettings", "Mesh", "Object"])

    def test_blenders_python_layer_members_are_found_with_their_signature(self):
        """Regression: Mesh.from_pydata was answered as not existing."""
        found = self.lookup(name="Mesh", member="from_pydata")["function"]
        self.assertEqual((found["kind"], found["signature"]), (
            "python_function", "from_pydata(self, vertices, edges, faces, shade_flat=False)"))
        self.assertIn("vertices, edges and faces", found["description"])
        self.assertEqual(self.lookup(name="Mesh", member="edge_keys")["function"]["kind"], "python_property")
        self.assertEqual(self.lookup(name="Mesh")["items"], [
            {"name": "edge_keys", "kind": "python_property"}, {"name": "from_pydata", "kind": "python_function"},
            {"name": "samples", "kind": "property", "type": "INT"}])
        with self.assertRaisesRegex(ValueError, "has no public property or function hidden"):
            self.lookup(name="Mesh", member="hidden")

    def test_unknown_and_unregistered_names_are_still_refused(self):
        for name in ("NoSuchSettings", "Unregistered", "bpy_struct"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "No installed RNA type named") as raised:
                self.lookup(name=name)
            self.assertEqual(refusal_code(raised.exception), "VALIDATION")

    def test_operators_are_found_by_either_name_without_running(self):
        """No operator, C or Python, is a bpy.types attribute (measured on Blender 5.2): a GPT 6.1 Sol chat on 1.0.6
        asked for one by its RNA name and was told the type was unknown, with error code UNKNOWN."""
        for name in ("object.smooth_by_angle", "OBJECT_OT_smooth_by_angle"):
            with self.subTest(name=name):
                found = self.lookup(name=name)
                self.assertEqual((found["name"], found["operator"]), ("OBJECT_OT_smooth_by_angle", "object.smooth_by_angle"))
                self.assertEqual(found["items"], [{"name": "angle", "kind": "property", "type": "FLOAT"}])
                self.assertEqual(self.lookup(name=name, member="angle")["property"]["hard_min"], 0.0)
        self.assertEqual(self.lookup(name="MESH_OT_primitive_cube_add")["operator"], "mesh.primitive_cube_add")
        self.assertEqual(self.ran, [], "a lookup never runs an operator")

    def test_every_miss_is_a_validation_refusal_naming_what_was_looked_for(self):
        misses = {("RNA", "object.no_such_operator", ""): "object.no_such_operator",
                  ("RNA", "NOSUCH_OT_thing", ""): "nosuch.thing",
                  ("RNA", "Mesh", "hidden"): "hidden",
                  ("RNA", "OBJECT_OT_smooth_by_angle", "missing"): "missing",
                  ("BMESH", "no_such_bmesh_op", ""): "no_such_bmesh_op",
                  ("WRITABLE", "NoSuchSettings", ""): "NoSuchSettings",
                  ("WRITABLE", "object.smooth_by_angle", ""): "object.smooth_by_angle"}
        for (domain, name, member), named in misses.items():
            with self.subTest(domain=domain, name=name, member=member):
                with self.assertRaises(ValueError) as raised:
                    self.lookup(domain=domain, name=name, member=member)
                self.assertEqual(refusal_code(raised.exception), "VALIDATION")
                self.assertIn(named, str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            self.lookup(name="NoSuchSettings")
        for form in ("module.name", "MODULE_OT_name"):
            self.assertIn(form, str(raised.exception), "an unknown name says which forms the lookup accepts")

    def test_an_invalid_argument_is_a_validation_refusal(self):
        with self.assertRaises(ValueError) as raised:
            self.lookup(name="Object.modifiers")
        self.assertEqual(refusal_code(raised.exception), "VALIDATION")

    def test_writable_names_the_owner_instead_of_calling_the_type_unknown(self):
        with self.assertRaisesRegex(ValueError, "through the type that owns them, for example Scene"):
            self.lookup(domain="WRITABLE", name="CyclesRenderSettings")


if __name__ == '__main__':
    unittest.main()
