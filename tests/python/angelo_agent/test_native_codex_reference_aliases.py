# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reference-local aliases preserve complete type nodes and ordinary rendering."""

import re
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_reference


ALIASES = ("Obj", "Vec3", "RGBA", "FaceSelector", "SubdivisionSettings", "MirrorSettings", "CameraSettings",
           "LightSettings")
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[A-Za-z_$][A-Za-z0-9_$]*|.', re.DOTALL)


def expand_types(text, definitions):
    """Independent token expansion; quoted literals and property names are not types.

    A union alias (Obj) before ``[]`` is parenthesized, as the renderer writes a union's array.
    """
    tokens = TOKENS.findall(text)
    result = []
    for index, token in enumerate(tokens):
        following = "".join(tokens[index + 1:index + 3])
        is_property = following.startswith(":") or following.startswith("?:")
        if token in definitions and not is_property:
            expanded = expand_types(definitions[token], definitions)
            result.append("(%s)" % expanded if following == "[]" and "|" in expanded else expanded)
        else:
            result.append(token)
    return "".join(result)


class ReferenceAliasTests(unittest.TestCase):
    def test_aliases_are_local_ordered_and_expand_to_every_original_byte(self):
        for short in (True, False):
            with self.subTest(short_identifiers=short):
                reference = codex_reference.program_reference(short)
                lines = reference.splitlines()
                definitions = {}
                for name, line in zip(ALIASES, lines[1:1 + len(ALIASES)]):
                    self.assertTrue(line.startswith("type " + name + "="))
                    definitions[name] = line[len("type " + name + "="):-1]
                body = "\n".join(lines[:1] + lines[1 + len(ALIASES):])
                expanded = expand_types(body, definitions)
                with patch.object(codex_reference, "_reference_aliases", return_value=({}, [])):
                    ordinary = codex_reference.program_reference(short)
                self.assertEqual(expanded, ordinary)
                for name in ALIASES:
                    self.assertGreater(sum(token == name for token in TOKENS.findall(body)), 0, name)
                self.assertLess(len(reference.encode()), len(ordinary.encode()))

    def test_alias_matching_does_not_replace_literals_or_property_names(self):
        aliases, _lines = codex_reference._reference_aliases()
        vector = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
        schema = {"type": "object", "properties": {
            "Vec3": vector, "[number,number,number]": {"const": "[number,number,number]"},
            "RGBA": {"enum": ["Vec3", "RGBA"]}}, "additionalProperties": False}
        self.assertEqual(codex_reference._typescript(schema, {}, aliases),
                         '{Vec3?:Vec3;"[number,number,number]"?:"[number,number,number]";RGBA?:"Vec3"|"RGBA";}')
        self.assertEqual(codex_reference.typescript(schema),
                         '{Vec3?:[number,number,number];"[number,number,number]"?:"[number,number,number]";RGBA?:"Vec3"|"RGBA";}')

    def test_nearby_shapes_are_not_aliased(self):
        aliases, _lines = codex_reference._reference_aliases()
        for count, item, expected in [(2, "number", "[number,number]"), (3, "string", "[string,string,string]"),
                                      (3, "boolean", "[boolean,boolean,boolean]"), (5, "number", "number[]")]:
            schema = {"type": "array", "items": {"type": item}, "minItems": count, "maxItems": count}
            self.assertEqual(codex_reference._typescript(schema, {}, aliases), expected)

    def test_aliases_resolve_local_refs_and_preserve_parentheses_and_maps(self):
        aliases, _lines = codex_reference._reference_aliases()
        vector = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
        definitions = {"v": vector}
        schema = {"type": "array", "items": {"oneOf": [{"$ref": "#/$defs/v"}, {"type": "null"}]}}
        self.assertEqual(codex_reference._typescript(schema, definitions, aliases), "(Vec3|null)[]")
        self.assertEqual(codex_reference.typescript(schema, definitions), "([number,number,number]|null)[]")
        schema = {"type": "object", "properties": {"fixed": vector}, "additionalProperties": vector}
        self.assertEqual(codex_reference._typescript(schema, {}, aliases), "{fixed?:Vec3;[k:string]:Vec3;}")

    def test_job_declaration_keeps_its_root_definitions(self):
        from angelo_agent.tool_specs import TOOL_MAP
        vector = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
        schema = {"$defs": {"name": {"type": "string"}, "offset": vector}, "type": "object",
                  "properties": {"target": {"$ref": "#/$defs/name"}, "offset": {"$ref": "#/$defs/offset"}},
                  "required": ["target"], "additionalProperties": False}
        aliases, _lines = codex_reference._reference_aliases()
        with patch.dict(TOOL_MAP, {codex_reference.JOB_TOOL: {"inputSchema": schema}}):
            ordinary = codex_reference._scene_lines()[-1]
            aliased = codex_reference._scene_lines(aliases)[-1]
        self.assertTrue(ordinary.endswith("{target:string;offset?:[number,number,number];}."))
        self.assertTrue(aliased.endswith("{target:string;offset?:Vec3;}."))


if __name__ == "__main__":
    unittest.main()
