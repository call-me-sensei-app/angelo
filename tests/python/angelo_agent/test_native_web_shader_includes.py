# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Multi-file GLSL web shaders (doc/angelo/web_export.rst, "Web shaders"): ``#include "file.glsl"`` is expanded inside
the project folder only, with cycles, depth and size refused by name, and every file of the expansion is listed so a
save of any of them reloads the page.

Real use (2026-11-15): a toon material split into a main file and shared lighting and ramp files could not preview,
because the page took one file. Shader libraries are commonly split this way, so the rule is general: any GLSL
project folder, never a particular library's layout."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_shader_spec as spec  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402


class IncludeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def expand(self, path, root=None):
        return spec.expand_includes(str(path), str(root or self.root))

    def test_includes_expand_in_place_once_each(self):
        self.write("lib/ramp.glsl", "float ramp(float x) { return step(0.5, x); }\n")
        self.write("lib/light.glsl", '#include "ramp.glsl"\nfloat light(vec3 n) { return ramp(n.y); }\n')
        main = self.write("shaders/toon.frag",
                          '#include "../lib/light.glsl"\n  #include "../lib/ramp.glsl"\nvoid main() {\n')
        result = self.expand(main)
        self.assertIsNone(result["problem"], result)
        source = result["source"]
        self.assertEqual(source.count("float ramp("), 1)   # A file included twice is pasted once.
        self.assertLess(source.index("float ramp("), source.index("float light("))
        self.assertTrue(source.rstrip().endswith("void main() {"))
        self.assertEqual([Path(item).name for item in result["files"]], ["toon.frag", "light.glsl", "ramp.glsl"])

    def test_a_file_without_includes_is_unchanged(self):
        main = self.write("plain.glsl", "uniform float gain; // 0 4\nvoid main() {\n")
        result = self.expand(main)
        self.assertEqual(result["source"], "uniform float gain; // 0 4\nvoid main() {\n")
        self.assertEqual(result["files"], [str(main)])

    def test_uniforms_in_included_files_are_editable_too(self):
        self.write("tint.glsl", "uniform vec3 shade_tint; // color\n")
        main = self.write("main.glsl", '#include "tint.glsl"\nuniform float bands; // 1 8 = 3\nvoid main() {\n')
        names = [item[0] for item in spec.file_uniforms(self.expand(main)["source"])]
        self.assertEqual(names, ["shade_tint", "bands"])

    def test_refusals_name_the_file_and_the_reason(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: (outside / "secret.glsl").unlink(missing_ok=True) or outside.rmdir())
        (outside / "secret.glsl").write_text("float x;\n", encoding="utf-8")
        cases = {
            "missing": '#include "nope.glsl"\n',
            "outside": '#include "../%s/secret.glsl"\n' % os.path.relpath(outside, self.root.parent),
            "absolute": '#include "%s"\n' % (outside / "secret.glsl"),
            "language": '#include "notes.txt"\n',
        }
        self.write("notes.txt", "hello\n")
        expected = {"missing": "include_missing", "outside": "include_outside", "absolute": "include_outside",
                    "language": "include_language"}
        for name, text in cases.items():
            main = self.write(name + ".glsl", text + "void main() {\n")
            result = self.expand(main)
            self.assertEqual(result["problem"], expected[name], (name, result))
            self.assertIsNone(result["source"])
            self.assertTrue(result["detail"], name)
            self.assertNotIn(str(outside), result["detail"])   # Never a path outside the project in a message.

    def test_a_link_that_leaves_the_folder_is_outside(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: (outside / "x.glsl").unlink(missing_ok=True) or outside.rmdir())
        (outside / "x.glsl").write_text("float x;\n", encoding="utf-8")
        try:
            (self.root / "link.glsl").symlink_to(outside / "x.glsl")
        except (OSError, NotImplementedError):
            self.skipTest(environment_skip("symbolic links aren't available here"))
        main = self.write("main.glsl", '#include "link.glsl"\nvoid main() {\n')
        self.assertEqual(self.expand(main)["problem"], "include_outside")

    def test_a_cycle_and_a_deep_chain_are_refused(self):
        self.write("a.glsl", '#include "b.glsl"\n')
        self.write("b.glsl", '#include "a.glsl"\n')
        self.assertEqual(self.expand(self.root / "a.glsl")["problem"], "include_cycle")
        for index in range(spec.MAX_INCLUDE_DEPTH + 2):
            self.write("d%d.glsl" % index, '#include "d%d.glsl"\n' % (index + 1))
        self.write("d%d.glsl" % (spec.MAX_INCLUDE_DEPTH + 2), "float end;\n")
        self.assertEqual(self.expand(self.root / "d0.glsl")["problem"], "include_depth")

    def test_the_whole_expansion_counts_toward_the_size_limit(self):
        half = "// " + "x" * (spec.MAX_SOURCE // 2) + "\n"
        self.write("one.glsl", half)
        self.write("two.glsl", half)
        main = self.write("main.glsl", '#include "one.glsl"\n#include "two.glsl"\nvoid main() {\n')
        self.assertEqual(self.expand(main)["problem"], "too_large")

    def test_an_include_inside_a_comment_or_string_shape_is_left_alone(self):
        main = self.write("main.glsl", '// #include "nope.glsl"\n#define NOTE "#include x"\nvoid main() {\n')
        self.assertIsNone(self.expand(main)["problem"])


if __name__ == "__main__":
    unittest.main()
