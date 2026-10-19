# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The closed path grammar of angelo_read_data: what parses, and that nothing else does. No Blender."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import data_path  # noqa: E402
from angelo_agent.data_path import ATTRIBUTE, DATA, INDEX, KEY, SCENE, DataPathError, parse, quote, render  # noqa: E402


class GrammarTests(unittest.TestCase):
    def test_roots_and_their_console_spellings(self):
        self.assertEqual(parse("scene"), (SCENE, []))
        self.assertEqual(parse("data"), (DATA, []))
        self.assertEqual(parse("bpy.context.scene.cycles"), (SCENE, [(ATTRIBUTE, "cycles")]))
        self.assertEqual(parse("bpy.data.objects"), (DATA, [(ATTRIBUTE, "objects")]))
        self.assertEqual(parse("  scene.render  "), (SCENE, [(ATTRIBUTE, "render")]))

    def test_steps_are_attributes_indexes_and_quoted_names(self):
        root, steps = parse('data.node_groups["Rock Form"].nodes[0].inputs[-1].default_value')
        self.assertEqual(root, DATA)
        self.assertEqual(steps, [(ATTRIBUTE, "node_groups"), (KEY, "Rock Form"), (ATTRIBUTE, "nodes"), (INDEX, 0),
                                 (ATTRIBUTE, "inputs"), (INDEX, -1), (ATTRIBUTE, "default_value")])
        self.assertEqual(parse("scene.objects['Cube']")[1], [(ATTRIBUTE, "objects"), (KEY, "Cube")])

    def test_keys_escape_only_their_quote_and_the_backslash(self):
        self.assertEqual(parse(r'data.objects["a\"b\\c"]')[1][-1], (KEY, 'a"b\\c'))
        self.assertEqual(parse(r"data.objects['it\'s']")[1][-1], (KEY, "it's"))
        self.assertEqual(parse('data.objects["Ünïcode 名前"]')[1][-1], (KEY, "Ünïcode 名前"))
        # Brackets, dots and parentheses inside a quoted name are part of the name.
        self.assertEqual(parse('data.objects["a].b(c)"]')[1][-1], (KEY, "a].b(c)"))
        for bad in (r'data.objects["a\nb"]', r'data.objects["a\x41"]', 'data.objects["a\tb"]', 'data.objects[""]'):
            with self.subTest(path=bad), self.assertRaises(DataPathError):
                parse(bad)

    def test_render_is_canonical_and_round_trips(self):
        for text in ("scene", "data.objects", 'data.objects["a\\"b\\\\c"].modifiers[-1]',
                     'scene.objects["Island"].modifiers["Rock Form"].properties.inputs'):
            with self.subTest(path=text):
                root, steps = parse(text)
                self.assertEqual(render(root, steps), text)
                self.assertEqual(parse(render(root, steps)), (root, steps))
        self.assertEqual(render(*parse("bpy.data.objects['Cube']")), 'data.objects["Cube"]')
        self.assertEqual(quote('a"b'), '"a\\"b"')

    def test_nothing_but_the_grammar_parses(self):
        """Every way Python reaches further than an attribute or a key is a refusal, with a stable code."""
        cases = {
            "scene.objects.get('Cube')": "CALL",
            "scene.statistics(view_layer)": "CALL",
            "__import__('os').system('id')": "ROOT",
            "bpy.ops.wm.quit_blender()": "ROOT",
            "bpy.context.window_manager": "ROOT",
            "bpy.context.preferences.filepaths": "ROOT",
            "bpy.app.binary_path": "ROOT",
            "context.scene": "ROOT",
            "scenery": "ROOT",
            "bpy.datax": "ROOT",
            "": "EMPTY",
            "   ": "EMPTY",
            "scene.__class__": "PRIVATE_NAME",
            "scene._private": "PRIVATE_NAME",
            "scene.objects[0].__dict__": "PRIVATE_NAME",
            "scene.objects[0:3]": "SYNTAX",
            "scene.objects[:]": "SYNTAX",
            "scene.objects[1+1]": "SYNTAX",
            "scene.objects[1e3]": "SYNTAX",
            "scene.objects[0x10]": "SYNTAX",
            "scene.objects[ 0 ]": "SYNTAX",
            "scene.objects[]": "SYNTAX",
            "scene.objects[a]": "SYNTAX",
            'scene.objects["Cube"': "SYNTAX",
            'scene.objects["Cube]': "SYNTAX",
            "scene.objects[f'{1}']x": "SYNTAX",
            "scene . render": "SYNTAX",
            "scene.render;import os": "SYNTAX",
            "scene.render\nscene.cycles": "SYNTAX",
            "scene.render or data": "SYNTAX",
            "scene.render + 1": "SYNTAX",
            "scene.frame_current == 1": "SYNTAX",
            "scene.render.": "SYNTAX",
            "scene..render": "SYNTAX",
            "scene.9lives": "SYNTAX",
            "scene.objects[12345678]": "SYNTAX",
            "scene.café": "SYNTAX",
            "[x for x in data.objects]": "ROOT",
            "lambda: scene": "ROOT",
        }
        for text, code in cases.items():
            with self.subTest(path=text):
                with self.assertRaises(DataPathError) as raised:
                    parse(text)
                self.assertEqual(raised.exception.code, code)
                self.assertIsInstance(raised.exception.position, int)

    def test_bounds(self):
        self.assertEqual(len(parse("scene" + ".a" * data_path.MAX_STEPS)[1]), data_path.MAX_STEPS)
        with self.assertRaises(DataPathError) as raised:
            parse("scene" + ".a" * (data_path.MAX_STEPS + 1))
        self.assertEqual(raised.exception.code, "TOO_DEEP")
        with self.assertRaises(DataPathError) as raised:
            parse("scene." + "a" * data_path.MAX_PATH_CHARS)
        self.assertEqual(raised.exception.code, "TOO_LONG")
        with self.assertRaises(DataPathError):
            parse("scene." + "a" * (data_path.MAX_NAME_CHARS + 1))
        with self.assertRaises(DataPathError):
            parse('data.objects["%s"]' % ("x" * (data_path.MAX_KEY_CHARS + 1)))
        self.assertEqual(parse("scene.objects[%d]" % data_path.MAX_INDEX)[1][-1], (INDEX, data_path.MAX_INDEX))

    def test_non_text_is_refused_not_coerced(self):
        for value in (None, 3, ["scene"], {"path": "scene"}, b"scene"):
            with self.subTest(value=value), self.assertRaises(DataPathError):
                parse(value)

    def test_the_parser_never_evaluates(self):
        """The module has no eval, exec, compile, getattr or import machinery to reach."""
        source = Path(data_path.__file__).read_text()
        code = "\n".join(line for line in source.split('"""')[2].splitlines() if not line.lstrip().startswith("#"))
        for forbidden in ("eval(", "exec(", "compile(", "getattr(", "__import__", "importlib", "ast."):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
