# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One table of how each Blender attribute type is read (``angelo_agent.attribute_types``), used by every reader.

Three modules kept their own copy (``scene_geometry``'s exact program read, ``operator_state``'s before/after state,
``export_baked_reads``), and the copies drifted: INT16_2D (tangent-space custom normals) was missing from the
first, so every typed program on an imported mesh refused, and FLOAT4 from all three. Covered (no Blender):

* every type Blender defines (``rna_enum_attribute_type_items`` in the source) is read or named unreadable;
* the three readers take their tables from the shared one, with exactly the values they read before (their
  revisions and digests do not move);
* no other module of the add-on keeps its own table of attribute types.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def blender_types():
    """The identifiers of ``rna_enum_attribute_type_items`` in this source tree."""
    source = (ROOT / "source/blender/makesrna/intern/rna_attribute.cc").read_text(encoding="utf-8")
    start = source.index("const EnumPropertyItem rna_enum_attribute_type_items[] = {")
    body = source[start:source.index("};", start)]
    return set(re.findall(r'\{\s*CD_PROP_\w+,\s*"([A-Z0-9_]+)"', body))


# The values each reader used before the shared table (its revisions and digests depend on them).
SCENE_GEOMETRY = {"FLOAT": ("value", "f", 1), "INT": ("value", "i", 1), "INT8": ("value", "b", 1),
                  "BOOLEAN": ("value", "b", 1), "FLOAT_VECTOR": ("vector", "f", 3), "FLOAT2": ("vector", "f", 2),
                  "FLOAT4": ("vector", "f", 4), "FLOAT_COLOR": ("color", "f", 4), "BYTE_COLOR": ("color", "f", 4),
                  "QUATERNION": ("value", "f", 4), "INT16_2D": ("value", "i", 2), "INT32_2D": ("value", "i", 2),
                  "FLOAT4X4": ("value", "f", 16)}
OPERATOR_STATE = dict(SCENE_GEOMETRY, INT8=("value", "i", 1))
EXPORT_BAKED = {name: (prop, width, kind) for name, (prop, kind, width) in {
    "FLOAT": ("value", "float32", 1), "INT": ("value", "int32", 1), "FLOAT_VECTOR": ("vector", "float32", 3),
    "FLOAT_COLOR": ("color", "float32", 4), "BYTE_COLOR": ("color", "float32", 4), "FLOAT2": ("vector", "float32", 2),
    "BOOLEAN": ("value", "bool", 1), "INT8": ("value", "int32", 1), "INT16_2D": ("value", "int32", 2),
    "INT32_2D": ("value", "int32", 2), "QUATERNION": ("value", "float32", 4), "FLOAT4X4": ("value", "float32", 16),
    "FLOAT4": ("vector", "float32", 4)}.items()}


class TableTests(unittest.TestCase):
    def test_every_type_blender_defines_is_read_or_named_unreadable(self):
        from angelo_agent import attribute_types
        defined = blender_types()
        self.assertGreaterEqual(len(defined), 13, "the source's enum was found")
        covered = set(attribute_types.FIELDS) | set(attribute_types.UNREAD)
        self.assertEqual(sorted(defined - covered), [])
        self.assertEqual(sorted(set(attribute_types.FIELDS) & set(attribute_types.UNREAD)), [])

    def test_the_readers_take_the_shared_table_with_the_values_they_read_before(self):
        from angelo_agent import operator_state, scene_geometry
        self.assertEqual(scene_geometry.ATTRIBUTE_READS, SCENE_GEOMETRY)
        self.assertEqual(operator_state._ATTRIBUTE_LAYOUT, OPERATOR_STATE)
        import numpy
        with patch.dict(sys.modules, {"bpy": MagicMock()}):
            sys.modules.pop("angelo_agent.export_baked_reads", None)
            from angelo_agent import export_baked_reads
        found = {name: (prop, width, numpy.dtype(kind).name) for name, (prop, width, kind) in
                 export_baked_reads.FIELDS.items()}
        self.assertEqual(found, EXPORT_BAKED)


class SourceTests(unittest.TestCase):
    def test_no_module_keeps_its_own_table_of_attribute_types(self):
        """A dict literal keyed by three or more of Blender's attribute types is a table: only attribute_types. The
        names attribute types share with RNA property types (BOOLEAN, INT, FLOAT, STRING) do not count: tables of
        property types (foreach_get item codes, stored-value checks) are not attribute tables."""
        types = blender_types() - {"BOOLEAN", "INT", "FLOAT", "STRING"}
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            if path.name == "attribute_types.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Dict):
                    keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if len(keys & types) >= 3:   # FLOAT_VECTOR, INT8, QUATERNION...
                        found.append("%s:%d" % (path.relative_to(ADDON), node.lineno))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
