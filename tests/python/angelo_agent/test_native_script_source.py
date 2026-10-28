# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script's text is compiled and checked for removed names once (script_source), however many times its
call is validated, and the executor runs that same code object: the text is the cache key, so a hit is never another
script's, a text that does not compile keeps nothing, and a parent-inverse statement still gets its host call."""

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import script_api_removed, script_prepare, script_source  # noqa: E402
from angelo_agent.script_errors import SCRIPT_FILE  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

PLAIN = "result = {'count': len(bpy.data.objects)}\n"
REMOVED = "import bpy\nbpy.ops.import_scene.obj(filepath='a.obj')\n"
INVERSE = "child.parent = rig\nchild.matrix_parent_inverse = rig.matrix_world.inverted()\n"


def call(code):
    return {"description": "Count the objects", "code": code}


class ScriptSourceTests(unittest.TestCase):
    def setUp(self):
        script_source.compiled.cache_clear()
        script_source._removed.cache_clear()

    def test_one_text_is_compiled_and_walked_once_across_validations(self):
        walks = mock.Mock(side_effect=script_api_removed.problems)
        with mock.patch.object(script_api_removed, "problems", walks), \
                mock.patch("builtins.compile", wraps=compile) as compiles:
            for _ in range(6):   # Admission, preconditions, the executor's two checks.
                self.assertEqual(validate_call("angelo_execute_python", call(PLAIN), host=True)["code"], PLAIN)
        self.assertEqual(walks.call_count, 1)
        self.assertEqual(sum(args[:2] == (PLAIN, SCRIPT_FILE) for args, _ in compiles.call_args_list), 1)

    def test_the_key_is_the_text_itself(self):
        first = script_source.compiled(PLAIN)
        self.assertIs(script_source.compiled((PLAIN + " ")[:-1]), first)   # An equal text from another source.
        other = script_source.compiled(PLAIN + "result = None\n")
        self.assertIsNot(other, first)
        self.assertEqual(first.co_filename, SCRIPT_FILE)
        namespace = {"bpy": mock.Mock(data=mock.Mock(objects=[1, 2]))}
        exec(first, namespace)
        self.assertEqual(namespace["result"], {"count": 2})
        again = {"bpy": mock.Mock(data=mock.Mock(objects=[1]))}
        exec(script_source.compiled(PLAIN), again)   # The shared code object runs in a fresh namespace.
        self.assertEqual(again["result"], {"count": 1})

    def test_the_cache_is_bounded(self):
        for index in range(script_source.SCRIPTS + 3):
            script_source.compiled("x = %d\n" % index)
        self.assertEqual(script_source.compiled.cache_info().currsize, script_source.SCRIPTS)

    def test_a_text_that_does_not_compile_raises_every_time_and_keeps_nothing(self):
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                validate_call("angelo_execute_python", call("result = (\n"), host=True)
            self.assertIn("Invalid Blender Python on line 1", str(caught.exception))
        self.assertEqual(script_source.compiled.cache_info().currsize, 0)

    def test_removed_names_refuse_every_time_with_their_own_list(self):
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                validate_call("angelo_execute_python", call(REMOVED), host=True)
            self.assertIn("import_scene.obj", str(caught.exception))
        found = script_source.removed(REMOVED)
        found.append("changed by a caller")
        self.assertEqual(script_source.removed(REMOVED), script_api_removed.problems(REMOVED))

    def test_the_executor_runs_the_code_validation_compiled(self):
        validate_call("angelo_execute_python", call(PLAIN), host=True)
        prepared, namespace = script_prepare.Prepared(None, None), {}
        with mock.patch.object(script_prepare.ast, "parse", side_effect=AssertionError("parsed again")):
            self.assertIs(prepared.compiled(PLAIN, namespace), script_source.compiled(PLAIN))
        self.assertNotIn(script_prepare.FRESH, namespace)

    def test_a_parent_inverse_statement_still_gets_its_host_call(self):
        for code in (INVERSE, INVERSE.replace("matrix_parent_inverse", "ｍatrix_parent_inverse")):
            namespace = {}   # The fullwidth spelling is the same name once Python normalizes it.
            compiled = script_prepare.Prepared(None, None).compiled(code, namespace)
            self.assertIn(script_prepare.FRESH, namespace)
            self.assertIn(script_prepare.FRESH, compiled.co_names)
        ascii_mention = "note = 'matrix_parent_inverse'\n"   # Named, never assigned: runs as written.
        namespace = {}
        self.assertIs(script_prepare.Prepared(None, None).compiled(ascii_mention, namespace),
                      script_source.compiled(ascii_mention))
        self.assertNotIn(script_prepare.FRESH, namespace)


if __name__ == "__main__":
    unittest.main()
