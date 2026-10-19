# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class CompiledEditorContracts(unittest.TestCase):
    def test_native_selection_does_not_rebuild_cpp_owned_buffers(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/editor_types.py").read_text()
        ast.parse(source)
        self.assertIn("if not native and (changed or space.language != target)", source)
        self.assertIn("if not native:\n                _buffers", source)

    def test_window_blend_drop_defers_to_ai_reference_handlers(self):
        source = (ROOT / "source/blender/editors/screen/screen_ops.cc").read_text()
        poll = source.split("static bool blend_file_drop_poll(", 1)[1].split("static void blend_file_drop_copy", 1)[0]
        self.assertIn("CTX_wm_space_console(C)", poll)
        self.assertIn('STREQ(console->language, "angelo")', poll)
        self.assertLess(poll.index("return false;"), poll.index("FILE_TYPE_BLENDER"))

    def test_chat_backing_buffers_are_not_serialized_into_blend_files(self):
        source = (ROOT / "source/blender/editors/space_console/space_console.cc").read_text()
        write = source.split("static void console_space_blend_write(", 1)[1].split("void ED_spacetype_console()", 1)[0]
        self.assertIn("saved.history.clear_no_delete()", write)
        self.assertIn("saved.scrollback.clear_no_delete()", write)
        self.assertIn("write_struct_at_address(con, &saved)", write)

    def test_alternate_buffer_storage_is_freed_before_allocator_shutdown(self):
        source = (ROOT / "source/blender/editors/space_console/space_console.cc").read_text()
        self.assertIn("console_alternate_buffers.clear()", source)

    def test_preferences_do_not_require_an_already_enabled_host(self):
        source = (ROOT / "scripts/startup/bl_ui/space_userpref.py").read_text()
        panel = source.split("class USERPREF_PT_ai_providers", 1)[1].split("class USERPREF_HT_header", 1)[0]
        self.assertIn('"angelo_agent" not in context.preferences.addons', panel)
        self.assertIn('"preferences.addon_enable"', panel)

    def test_compiled_launcher_keeps_native_branding_menus(self):
        source = (ROOT / "scripts/angelo/launch.py").read_text()
        self.assertIn("if not editor_types.native_picker():\n    preview_branding.install()", source)

    def test_ai_subtypes_do_not_advertise_the_python_shortcut(self):
        source = (ROOT / "source/blender/editors/interface/interface.cc").read_text()
        self.assertIn("(ui_type >> 16) == SPACE_CONSOLE && (ui_type & 0xffff) != 0", source)


if __name__ == "__main__":
    unittest.main()
