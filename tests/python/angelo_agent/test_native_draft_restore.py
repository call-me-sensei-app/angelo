# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.drafts import read_chat_draft, write_chat_draft
from angelo_agent.editor_registry import cached_editor_identity


class DraftAccessTests(unittest.TestCase):
    def test_hidden_native_input_updates_without_touching_python(self):
        coding = SimpleNamespace(body="Chat A 🧊", current_character=5)
        python = SimpleNamespace(body="print('keep Python')", current_character=6)
        space = SimpleNamespace(language="python", history=[python], coding_agent_input=coding)
        self.assertEqual(read_chat_draft(space, {}), ("Chat A 🧊", 5))
        write_chat_draft(space, ("", 0), {})
        self.assertEqual((coding.body, coding.current_character), ("", 0))
        self.assertEqual((python.body, python.current_character), ("print('keep Python')", 6))

    def test_generative_input_is_not_mistaken_for_chat_input(self):
        generation = SimpleNamespace(body="image prompt", current_character=3)
        coding = SimpleNamespace(body="chat prompt", current_character=4)
        space = SimpleNamespace(language="angelo_genai", history=[generation], coding_agent_input=coding)
        write_chat_draft(space, ("Chat B", 2), {})
        self.assertEqual(read_chat_draft(space, {}), ("Chat B", 2))
        self.assertEqual(generation.body, "image prompt")

    def test_uninitialized_native_input_does_not_fall_back_to_python_or_preview(self):
        python = SimpleNamespace(body="never change", current_character=2)
        space = SimpleNamespace(language="python", history=[python], coding_agent_input=None, as_pointer=lambda: 1)
        buffers = {1: {"angelo": {"history": [("old preview", 1)]}}}
        self.assertIsNone(read_chat_draft(space, buffers))
        write_chat_draft(space, ("new", 2), buffers)
        self.assertEqual(python.body, "never change")
        self.assertEqual(buffers[1]["angelo"]["history"], [("old preview", 1)])

    def test_stock_preview_hidden_draft_updates_only_its_saved_chat_buffer(self):
        python = SimpleNamespace(body="Python", current_character=2)
        space = SimpleNamespace(language="python", history=[python], as_pointer=lambda: 7)
        buffers = {7: {"angelo": {"history": [("sent", 1), ("Chat A", 3)]}}}
        self.assertEqual(read_chat_draft(space, buffers), ("Chat A", 3))
        write_chat_draft(space, ("Chat B", 4), buffers)
        self.assertEqual(buffers[7]["angelo"]["history"], [("sent", 1), ("Chat B", 4)])
        self.assertEqual(python.body, "Python")

    def test_stock_visible_chat_draft_uses_its_real_line(self):
        line = SimpleNamespace(body="old", current_character=1)
        space = SimpleNamespace(language="angelo", history=[line])
        write_chat_draft(space, ("new", 2), {})
        self.assertEqual(read_chat_draft(space, {}), ("new", 2))


class CachedEditorIdentityTests(unittest.TestCase):
    def test_graph_editors_keep_their_subtype(self):
        for mode in ("DRIVERS", "FCURVES"):
            self.assertEqual(cached_editor_identity(SimpleNamespace(type="GRAPH_EDITOR", mode=mode)), mode)

    def test_file_editors_keep_their_subtype(self):
        for mode in ("ASSETS", "FILES"):
            self.assertEqual(cached_editor_identity(SimpleNamespace(type="FILE_BROWSER", browse_mode=mode)), mode)

    def test_node_editors_keep_their_tree_type(self):
        for tree in ("GeometryNodeTree", "CompositorNodeTree", "ShaderNodeTree", "TextureNodeTree", "CustomNodeTree"):
            self.assertEqual(cached_editor_identity(SimpleNamespace(type="NODE_EDITOR", tree_type=tree)), tree)

    def test_image_and_uv_editors_are_distinct(self):
        for mode, identity in (("UV", "UV"), ("VIEW", "IMAGE_EDITOR"), ("PAINT", "IMAGE_EDITOR"), ("MASK", "IMAGE_EDITOR")):
            # Blender's UI-only enum does not include UV; use the full mode.
            self.assertEqual(cached_editor_identity(SimpleNamespace(type="IMAGE_EDITOR", mode=mode, ui_mode="" if mode == "UV" else mode)), identity)

    def test_timeline_and_do_sheet_modes_are_distinct(self):
        for mode, identity in (("TIMELINE", "TIMELINE"), ("ACTION", "DOPESHEET"), ("DOPESHEET", "DOPESHEET")):
            self.assertEqual(cached_editor_identity(SimpleNamespace(type="DOPESHEET_EDITOR", mode=mode)), identity)

    def test_other_editor_types_are_unchanged(self):
        for kind in ("VIEW_3D", "PROPERTIES", "TEXT_EDITOR", "OUTLINER"):
            self.assertEqual(cached_editor_identity(SimpleNamespace(type=kind)), kind)

    def test_cmake_tests_and_libraries_follow_the_actual_app_target(self):
        for file in ("tests/CMakeLists.txt", "build_files/cmake/platform/platform_apple.cmake", "build_files/cmake/testing.cmake"):
            source = (ROOT / file).read_text()
            self.assertNotIn("/Blender.app/", source)
            self.assertIn("$<TARGET_FILE_BASE_NAME:blender>", source)


if __name__ == "__main__":
    unittest.main()
