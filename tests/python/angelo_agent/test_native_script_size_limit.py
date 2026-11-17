# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An agent knows how long a script may be before it writes one, and a refusal for length says how to go on.

A script holds at most ``script_store.MAX_CODE`` characters. Up to the 2.0.0 candidate the agent met that limit only
as a refusal after it had written the whole script ("A Blender script requires 1-32768 characters, without NUL bytes
(this one has N)"), which names no way on, so a showcase run split its script after the fact (2026-11-14, F3). The
number was also written out separately in the schema, the validation, the script-file reader and an app's tool
description.

Pinned:

* one constant: the schema's ``maxLength``, the validation, the patch builder and the script-file reader all read
  ``script_store.MAX_CODE``, and none of those modules writes the number out;
* where the agent decides: the description of ``angelo_execute_python``, and of an app's script-file tool, states
  the limit (that constant) and that longer work is split;
* a refusal for length (a script sent in full, a patch that grows one past it, a script file) says the size, the
  limit and how to proceed: split the work into steps, or keep bulk data in a file the script reads. The other
  refusals of a script (empty, a NUL byte) do not give that advice.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import external_mcp_tools, script_files, script_store, tool_specs  # noqa: E402

LIMIT = script_store.MAX_CODE
SHOWN = format(LIMIT, ",")
WAYS_ON = ("split", "step", "file the script reads")


def says_how(test, message, size):
    """A refusal for length: the size, the limit and the ways on."""
    test.assertIn(format(size, ","), message)
    test.assertIn(SHOWN, message)
    for words in WAYS_ON:
        test.assertIn(words, message.lower(), message)


def script(length):
    """A valid Python script of exactly ``length`` characters."""
    line = "result = {'ok': True}\n"
    return line + "#" * (length - len(line))


class OneConstant(unittest.TestCase):
    def test_every_reader_of_the_limit_reads_the_one_constant(self):
        schema = tool_specs.TOOL_MAP["angelo_execute_python"]["inputSchema"]["properties"]["code"]
        self.assertEqual(schema["maxLength"], LIMIT)
        self.assertIs(script_files.MAX_CHARACTERS, script_store.MAX_CODE)

    def test_no_other_module_writes_the_number_out(self):
        written = []
        for name in ("tool_specs.py", "script_files.py", "external_mcp_tools.py", "external_work.py"):
            tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and (
                        node.value == LIMIT and type(node.value) is int
                        or isinstance(node.value, str) and (str(LIMIT) in node.value or SHOWN in node.value)):
                    written.append("%s:%d" % (name, node.lineno))
        self.assertEqual(written, [], "read script_store.MAX_CODE")


class WhereTheAgentDecides(unittest.TestCase):
    def test_the_script_tool_states_its_limit_and_that_longer_work_is_split(self):
        description = tool_specs.TOOL_MAP["angelo_execute_python"]["description"]
        self.assertIn(SHOWN + " characters", description)
        self.assertIn("split", description.lower())

    def test_an_apps_script_file_tool_states_the_same_limit(self):
        tool = next(item for item in external_mcp_tools.SCRIPT_FILE_TOOLS)
        self.assertIn(SHOWN + " characters", tool["description"])
        self.assertIn("split", tool["description"].lower())


class ARefusalForLength(unittest.TestCase):
    def test_a_script_at_the_limit_is_accepted_and_one_character_more_says_how_to_go_on(self):
        tool_specs.validate_call("angelo_execute_python", {"description": "A step", "code": script(LIMIT)})
        for size in (LIMIT + 1, LIMIT * 2 + 17):
            with self.subTest(size=size), self.assertRaises(ValueError) as refused:
                tool_specs.validate_call("angelo_execute_python", {"description": "A step", "code": script(size)})
            says_how(self, str(refused.exception), size)

    def test_other_refusals_of_a_script_do_not_talk_about_length(self):
        for code in ("", "   \n", "x = 1\x00"):
            with self.subTest(code=code), self.assertRaises(ValueError) as refused:
                tool_specs.validate_call("angelo_execute_python", {"description": "A step", "code": code})
            self.assertNotIn("split", str(refused.exception).lower())

    def test_a_patch_that_grows_a_script_past_the_limit_says_how_to_go_on(self):
        base = script(LIMIT - 10)
        with self.assertRaises(script_store.ScriptStoreError) as refused:
            script_store.patched(base, [{"find": "result = {'ok': True}",
                                         "replace": "result = {'ok': True, 'n': 12345}"}])
        says_how(self, str(refused.exception), LIMIT - 10 + len(", 'n': 12345"))
        self.assertIn("edits[0]", str(refused.exception))

    def test_a_script_file_longer_than_one_script_says_how_to_go_on(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "long.py"
            # Its line ends as written here on every platform: Windows' text mode would add a character to each.
            path.write_text(script(LIMIT + 5), encoding="utf-8", newline="\n")
            with self.assertRaises(script_files.ScriptFileError) as refused:
                script_files.read_script(str(path))
            says_how(self, str(refused.exception), LIMIT + 5)
            huge = Path(folder) / "huge.py"
            huge.write_bytes(b"#" * (LIMIT * 4 + 1))   # Refused by its size in bytes, before it is decoded.
            with self.assertRaises(script_files.ScriptFileError) as refused:
                script_files.read_script(str(huge))
            message = str(refused.exception)
            self.assertIn(SHOWN, message)
            for words in WAYS_ON:
                self.assertIn(words, message.lower(), message)


if __name__ == "__main__":
    unittest.main()
