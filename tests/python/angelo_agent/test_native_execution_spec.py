# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent.execution_spec import EXECUTION_TOOLS, validate_execution


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

        fake = SimpleNamespace(types=SimpleNamespace(bpy_struct=bpy_struct, Object=Object, Mesh=Mesh),
                               app=SimpleNamespace(version_string="5.2.2 LTS"))
        patcher = patch.dict(sys.modules, {"bpy": fake})
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
        with self.assertRaisesRegex(ValueError, "no such public property or function"):
            self.lookup(name="Mesh", member="hidden")

    def test_unknown_and_unregistered_names_are_still_refused(self):
        for name in ("NoSuchSettings", "Unregistered", "bpy_struct"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unknown installed RNA type"):
                self.lookup(name=name)

    def test_writable_names_the_owner_instead_of_calling_the_type_unknown(self):
        with self.assertRaisesRegex(ValueError, "through the type that owns them, for example Scene"):
            self.lookup(domain="WRITABLE", name="CyclesRenderSettings")


if __name__ == '__main__':
    unittest.main()
