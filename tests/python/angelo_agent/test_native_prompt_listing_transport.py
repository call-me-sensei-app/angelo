# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lossless tool-listing transport: actual client shape, older prefix form, closed negatives."""
import suite_isolation  # noqa: F401  First: no real provider profiles.

import json
import os
import stat
import subprocess
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import prompt_recordings  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

END = "prompt-audit-listing-end"
SHARED = "Angelo (Hyperbond Studio) server facts.\n" + "Host approval and exact schemas are retained.\n" * 120


def fixture():
    original, tools = [], []
    for index, name in enumerate(("preview", "capabilities", "view", "program", "recipe")):
        full_name = "mcp__angelo__angelo_" + name
        before = "Different tool introduction %d.\n\n" % index
        after = "\n\nexec tool declaration:\n```ts\ndeclare const tools: { %s(args: {exact_%d: string}): Promise<CallToolResult>; };\n```" % (full_name, index)
        description = before + SHARED + after
        original.append({"name": full_name, "description": description})
        tools.append({"name": full_name, "before": before, "after": after,
                      "chars": len(description.encode("utf-16-le")) // 2})
    return original, {"format": "shared-interior-v1", "shared": SHARED,
                      "count": len(tools), "tools": tools, "end": END}


def received(value):
    text = value if isinstance(value, str) else json.dumps(value)
    lines = [{"body": {"input": [{"type": "custom_tool_call_output",
                                  "output": [{"type": "input_text", "text": text}]}]}}]
    return prompt_recordings._retrieved(lines)


class ListingTransport(unittest.TestCase):
    def test_actual_client_interior_shape_reconstructs_every_byte_and_schema(self):
        original, packed = fixture()
        entries, complete = received(packed)
        self.assertTrue(complete)
        self.assertEqual(entries, original)
        self.assertEqual([row["description"].encode() for row in entries],
                         [row["description"].encode() for row in original])
        self.assertEqual([row["name"] for row in entries], [row["name"] for row in original])
        for index, row in enumerate(entries):
            self.assertIn("exact_%d: string" % index, row["description"])
        self.assertLess(len(json.dumps(packed)), len(json.dumps(original)))

    def test_empty_shared_fallback_still_reconstructs_exactly(self):
        original, packed = fixture()
        packed["shared"] = ""
        for item, whole in zip(packed["tools"], original):
            item["before"], item["after"] = whole["description"], ""
        self.assertEqual(received(packed), (original, True))

    def test_existing_shared_prefix_form_remains_readable(self):
        value = {"shared": "Original server prefix.",
                 "tools": [{"name": "angelo", "description": "Original tool suffix."}], "end": END}
        self.assertEqual(received(value), ([{"name": "(shared description prefix)",
                                             "description": "Original server prefix."},
                                            {"name": "angelo", "description": "Original tool suffix."}], True))

    def test_existing_full_description_form_remains_readable(self):
        value = {"tools": [{"name": "angelo", "description": "Full original text."}], "end": END}
        self.assertEqual(received(value), (value["tools"], True))

    def test_unknown_format_refuses(self):
        _, packed = fixture()
        packed["format"] = "unknown-reference-form"
        self.assertEqual(received(packed), ([], False))

    def test_malformed_field_types_refuse_without_partial_entries(self):
        for field, value in (("before", 4), ("after", None), ("name", []), ("chars", True)):
            with self.subTest(field=field):
                _, packed = fixture()
                packed["tools"][-1][field] = value
                self.assertEqual(received(packed), ([], False))

    def test_reference_instead_of_actual_description_refuses(self):
        _, packed = fixture()
        packed["tools"][1] = {"ref": 0}
        self.assertEqual(received(packed), ([], False))

    def test_missing_end_marker_refuses(self):
        _, packed = fixture()
        del packed["end"]
        self.assertEqual(received(packed), ([], False))

    def test_runtime_truncation_with_visible_end_marker_still_refuses(self):
        _, packed = fixture()
        text = json.dumps(packed)
        cut = text[:len(text) // 2] + "...TRUNCATED..." + text[-80:]
        self.assertIn(END, cut)
        self.assertEqual(received(cut), ([], False))

    def test_duplicate_tool_name_refuses(self):
        _, packed = fixture()
        packed["tools"][-1]["name"] = packed["tools"][0]["name"]
        self.assertEqual(received(packed), ([], False))

    def test_removed_tool_with_original_count_refuses(self):
        _, packed = fixture()
        packed["tools"].pop()
        self.assertEqual(received(packed), ([], False))

    def test_corrupted_shared_length_refuses(self):
        _, packed = fixture()
        packed["shared"] += "missing data"
        self.assertEqual(received(packed), ([], False))

    def test_non_bmp_description_uses_javascript_utf16_units_losslessly(self):
        original, packed = fixture()
        packed["shared"] += " Telescope \U0001f52d"
        for item, whole in zip(packed["tools"], original):
            whole["description"] = item["before"] + packed["shared"] + item["after"]
            item["chars"] = len(whole["description"].encode("utf-16-le")) // 2
        self.assertEqual(received(packed), (original, True))

    def test_unknown_top_level_or_tool_field_refuses(self):
        for where in ("top", "tool"):
            with self.subTest(where=where):
                _, packed = fixture()
                target = packed if where == "top" else packed["tools"][0]
                target["ref"] = "unresolved"
                self.assertEqual(received(packed), ([], False))


def row_fixture():
    original, packed = fixture()
    packed["format"] = "shared-interior-rows-v1"
    packed["tools"] = [[tool[key] for key in ("name", "before", "after", "chars")]
                       for tool in packed["tools"]]
    return original, packed


class ListingRows(unittest.TestCase):
    def test_rows_reconstruct_all_values_names_and_schemas(self):
        original, packed = row_fixture()
        self.assertEqual(received(packed), (original, True))
        _, objects = fixture()
        self.assertLess(len(json.dumps(packed)), len(json.dumps(objects)))

    def test_row_length_and_reference_forms_refuse_without_partial_entries(self):
        for row in (None, {}, {"ref": 0}, "row", [], [0], ["name", "before", "after"],
                    ["name", "before", "after", 0, "extra"]):
            with self.subTest(row=row):
                _, packed = row_fixture()
                packed["tools"][-1] = row
                self.assertEqual(received(packed), ([], False))

    def test_row_position_types_and_integer_indexes_refuse(self):
        for position, invalid in ((0, 1), (0, None), (1, {"ref": 0}), (1, 0),
                                  (2, [0]), (2, None), (3, True), (3, "42"), (3, 1.0)):
            with self.subTest(position=position, invalid=invalid):
                _, packed = row_fixture()
                packed["tools"][-1][position] = invalid
                self.assertEqual(received(packed), ([], False))

    def test_row_empty_or_duplicate_names_refuse(self):
        for name in ("", "mcp__angelo__angelo_preview"):
            with self.subTest(name=name):
                _, packed = row_fixture()
                packed["tools"][-1][0] = name
                self.assertEqual(received(packed), ([], False))

    def test_row_count_end_format_and_unknown_fields_refuse(self):
        for field, value in (("count", True), ("count", 0), ("count", 4), ("count", 6),
                             ("end", "wrong"), ("format", "shared-interior-rows-v2"), ("format", []), ("format", {}),
                             ("shared", None), ("ref", 0)):
            with self.subTest(field=field, value=value):
                _, packed = row_fixture()
                packed[field] = value
                self.assertEqual(received(packed), ([], False))
        _, packed = row_fixture()
        del packed["end"]
        self.assertEqual(received(packed), ([], False))

    def test_row_corruption_and_truncation_with_end_marker_refuse(self):
        for position, value in ((1, "lost"), (2, "lost"), (3, -1)):
            with self.subTest(position=position):
                _, packed = row_fixture()
                packed["tools"][-1][position] = value
                self.assertEqual(received(packed), ([], False))
        _, packed = row_fixture()
        text = json.dumps(packed)
        cut = text[:len(text) // 2] + "...TRUNCATED..." + text[-80:]
        self.assertIn(END, cut)
        self.assertEqual(received(cut), ([], False))


# Optional local engine, never installed by the suite. Release qualification supplies and pins it.
NODE = os.environ.get("ANGELO_PROMPT_LISTING_NODE", "")


@unittest.skipUnless(NODE, environment_skip("actual listing writer requires explicit ANGELO_PROMPT_LISTING_NODE"))
class ListingWriterJavaScript(unittest.TestCase):
    def execute(self, tools):
        from prompt_route_probe import LIST_TOOLS
        node = Path(NODE)
        self.assertTrue(node.is_absolute(), "The explicit JavaScript engine must be an absolute path")
        self.assertTrue(stat.S_ISREG(node.lstat().st_mode), "The JavaScript engine must be a plain file")
        self.assertTrue(os.access(node, os.X_OK), "The JavaScript engine must be executable")
        harness = ("const fs = require('node:fs'); const vm = require('node:vm'); "
                   "const input = JSON.parse(fs.readFileSync(0, 'utf8')); const writes = []; "
                   "vm.runInNewContext(input.code, {ALL_TOOLS: input.tools, text: value => writes.push(value)}, "
                   "{timeout: 1000}); process.stdout.write(JSON.stringify(writes));")
        env = {key: os.environ[key] for key in ("HOME", "TMPDIR", "SYSTEMROOT", "WINDIR", "ANGELO_QA_PROCESS_OWNER") if key in os.environ}
        return subprocess.run([str(node), "--no-addons", "-e", harness],
                              input=json.dumps({"code": LIST_TOOLS, "tools": tools}), text=True,
                              encoding="utf-8", capture_output=True, timeout=5, env=env)

    def packed(self, tools):
        result = self.execute(tools)
        self.assertEqual(result.returncode, 0, result.stderr[:1000])
        self.assertLess(len(result.stdout), 256 * 1024)
        writes = json.loads(result.stdout)
        self.assertEqual(len(writes), 1)
        return json.loads(writes[0])

    def test_actual_writer_preserves_interior_schema_quotes_newlines_and_unicode(self):
        original, _ = fixture()
        shared = SHARED + "Literal \"quotes\", apostrophe ', backslash \\, newline\n and telescope \U0001f52d"
        for index, tool in enumerate(original):
            tool["description"] = ("Unrelated introduction %d.\n\n" % index + shared +
                                   "\n\nSchema %d: {value: string, label: 'caf\u00e9'}\n" % index)
        unrelated = {"name": "clock__curr_time", "description": "A separate server, not Angelo."}
        packed = self.packed([unrelated] + original)
        self.assertEqual(packed["format"], "shared-interior-rows-v1")
        self.assertEqual(packed["count"], len(original))
        self.assertEqual(packed["end"], END)
        self.assertEqual(packed["shared"], shared)
        self.assertEqual(received(packed), (original, True))
        self.assertEqual([row[0] for row in packed["tools"]], [row["name"] for row in original])
        self.assertLess(len(json.dumps(packed)), len(json.dumps(original)))

    def test_actual_writer_prefix_and_empty_shared_fallbacks_are_lossless(self):
        for descriptions in (("Prefix alpha", "Prefix beta"), ("First \"one\"\n", "Second \U0001f52d\n")):
            with self.subTest(descriptions=descriptions):
                tools = [{"name": "angelo_%d" % index, "description": description}
                         for index, description in enumerate(descriptions)]
                packed = self.packed(tools)
                self.assertEqual(received(packed), (tools, True))
                self.assertEqual(packed["count"], len(tools))
                self.assertEqual(packed["end"], END)

    def test_actual_writer_multiple_counts_preserves_every_held_out_value(self):
        for count in (1, 7, 23):
            with self.subTest(count=count):
                shared = "Shared ordinary server facts, quotes ' and \"; Unicode \u03bb \U0001f30d.\n" * 9
                tools = [{"name": "mcp__angelo__held_%d" % index,
                          "description": "Different preface %d.\n\n" % index + shared +
                          "\n\nSchema %d: {nested: {label: string}, values: number[]}\n" % index}
                         for index in range(count)]
                packed = self.packed(tools + [{"name": "unrelated_vendor", "description": "Other tools"}])
                self.assertEqual(packed["count"], count)
                self.assertEqual(packed["end"], END)
                self.assertEqual(received(packed), (tools, True))
                for row in packed["tools"]:
                    self.assertIsInstance(row, list)
                    self.assertEqual(len(row), 4)

    def test_actual_writer_refuses_empty_or_non_string_tool_descriptions(self):
        for tools in ([], [{"name": "angelo_invalid", "description": None}]):
            with self.subTest(tools=tools):
                result = self.execute(tools)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
