# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which custom properties are Angelo's own saved records (``angelo_own_properties.own``), and that every exporter
that writes custom properties asks.

Angelo's records on the person's data (where a library item came from, a character part, a render region) were
written into every exported file by the four exporters that write custom properties (competitor triage row B33;
``test_blender_export_internal_properties`` exports through each in a real Blender and reads the files). This host
test pins what a build is not needed for:

* the rule: the two reserved prefixes and exactly the pre-rename keys, so a person's key that only looks like one
  (``sensei_notes``, ``my_angelo_note``, ``angelo`` alone, ``Angelo_Note``) is theirs and is exported;
* the pre-rename keys are those ``angelo_agent.legacy_names`` reads, all of them;
* the C++ twin (``BKE_angelo_own_properties.hh``: USD and Alembic) holds the same prefixes and names;
* each exporter's loop over custom properties asks the rule (by its source: the Python exporters import ``bpy``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/modules"))
import angelo_own_properties  # noqa: E402
from angelo_agent import legacy_names  # noqa: E402

HEADER = ROOT / "source/blender/blenkernel/BKE_angelo_own_properties.hh"
# Each exporter's source, the loop that writes custom properties (its first line) and how it asks the rule.
PYTHON_EXPORTERS = {
    "scripts/addons_core/io_scene_gltf2/blender/com/extras.py": (
        ("for custom_property in blender_element.keys():", "angelo_own(custom_property)"),
        ("for sys_property in properties.keys():", "angelo_own(sys_property)")),
    "scripts/addons_core/io_scene_fbx/export_fbx_bin.py": (("for k, v in items:", "angelo_own(k)"),),
}
NATIVE_EXPORTERS = {
    "source/blender/io/usd/intern/usd_writer_abstract.cc": (
        "for (IDProperty *prop = properties->data.group.first(); prop; prop = prop->next) {",
        "bke::angelo::is_own_property(prop->name)"),
    "source/blender/io/alembic/exporter/abc_custom_props.cc": (
        "for (IDProperty &id_property : group->data.group) {", "bke::angelo::is_own_property(id_property.name)"),
}


class TheRule(unittest.TestCase):
    def test_angelos_own_records_by_prefix_whatever_follows_it(self):
        for name in ("angelo_library", "angelo_team_item", "angelo_character_part", "_angelo_parts_merged",
                     "angelo_render_region", "_angelo_native_graph_v1", "angelo_a_key_no_code_writes_yet",
                     "_angelo_another_one", "angelo_", "_angelo_"):
            with self.subTest(name=name):
                self.assertTrue(angelo_own_properties.own(name))

    def test_a_persons_own_property_is_never_one(self):
        for name in ("note", "sensei_notes", "sensei_director_notes", "_sensei_", "my_angelo_note", "angelo",
                     "_angelo", "Angelo_Note", "ANGELO_KEY", "x_angelo_", " angelo_padded", "", "gltf_angelo_", 7, None,
                     b"angelo_bytes"):
            with self.subTest(name=name):
                self.assertFalse(angelo_own_properties.own(name))

    def test_the_pre_rename_keys_are_the_ones_angelo_still_reads(self):
        pairs = [value for name, value in vars(legacy_names).items()
                 if name.isupper() and isinstance(value, tuple) and len(value) == 2
                 and all(isinstance(item, str) for item in value)]
        self.assertGreaterEqual(len(pairs), 5)
        self.assertEqual(angelo_own_properties.LEGACY, {legacy for _current, legacy in pairs})
        for current, legacy in pairs:
            self.assertTrue(angelo_own_properties.own(current), current)
            self.assertTrue(angelo_own_properties.own(legacy), legacy)

    def test_the_native_twin_holds_the_same_names(self):
        code = re.sub(r"/\*.*?\*/", "", HEADER.read_text(encoding="utf-8"), flags=re.S)
        literals = re.findall(r'"([^"\n]+)"sv', code)
        prefixes = code[code.index("prefix :"):code.index("legacy :")]
        self.assertEqual(tuple(re.findall(r'"([^"\n]+)"sv', prefixes)), angelo_own_properties.PREFIXES)
        self.assertEqual(set(literals) - set(angelo_own_properties.PREFIXES), set(angelo_own_properties.LEGACY))
        self.assertEqual(len(literals), len(angelo_own_properties.PREFIXES) + len(angelo_own_properties.LEGACY))


class EveryExporterAsks(unittest.TestCase):
    def body(self, text, loop, lines=8):
        """The first ``lines`` lines of the loop that starts with ``loop``."""
        self.assertEqual(text.count(loop), 1, loop)
        return "\n".join(text[text.index(loop):].splitlines()[:lines])

    def test_the_python_exporters_skip_angelos_records_in_every_loop_over_custom_properties(self):
        for path, loops in PYTHON_EXPORTERS.items():
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("from angelo_own_properties import own as angelo_own", text, path)
            for loop, question in loops:
                with self.subTest(path=path, loop=loop):
                    body = self.body(text, loop, lines=3)
                    self.assertIn(question, body)
                    self.assertIn("continue", body)

    def test_the_native_exporters_skip_angelos_records_before_they_write_one(self):
        for path, (loop, question) in NATIVE_EXPORTERS.items():
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn('#include "BKE_angelo_own_properties.hh"', text)
                body = self.body(text, loop, lines=4)
                self.assertIn(question, body)
                self.assertIn("continue;", body)

    def test_the_check_before_an_export_reads_what_the_export_writes(self):
        """``engine_preflight`` warns about custom properties that hold paths on this computer; one of Angelo's own
        records (a library item's source) is not exported, so it is not warned about."""
        text = (ROOT / "scripts/addons_core/angelo_agent/engine_facts.py").read_text(encoding="utf-8")
        function = text[text.index("def _path_properties("):text.index("def _faces(")]
        self.assertIn("from angelo_own_properties import own", function)
        self.assertIn("if own(key):\n                continue", function)

    def test_no_other_place_in_these_exporters_writes_a_custom_property(self):
        """A second place that writes custom properties must ask too. Known today: FBX writes them in one function
        (six ``custom=True`` calls, and one in a comment), Alembic in one file."""
        fbx = (ROOT / "scripts/addons_core/io_scene_fbx/export_fbx_bin.py").read_text(encoding="utf-8")
        self.assertEqual(fbx.count("custom=True"), 7, "FBX writes a custom property somewhere new")
        function = fbx[fbx.index("def fbx_data_element_custom_properties("):fbx.index("def fbx_data_empty_elements(")]
        self.assertEqual(function.count("custom=True"), 6)
        alembic = ROOT / "source/blender/io/alembic/exporter"
        writers = sorted(path.name for path in alembic.glob("*.cc")
                         if "IDProperty &" in path.read_text(encoding="utf-8"))
        self.assertEqual(writers, ["abc_custom_props.cc"])


if __name__ == "__main__":
    unittest.main()
