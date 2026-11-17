# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The header menu paths behind ``operator_run`` how-to cards are Blender's own menus, as search finds them. No Blender.

Covered (``learn_menu_paths``, built by ``learn_menu_paths_source``): the checked-in table is exactly what this
checkout's ``scripts/startup/bl_ui`` builds; every header menu the builder starts from exists there; and the builder's
rules, on menu sources written for the test: a path starts at a header menu and goes down only through submenus shown
under their own title; a submenu drawn under another text, a context menu (search leaves those out) and an entry
whose text is worked out at run time or translated in a context of its own give no path; an entry's properties are
read whether set on the call, on a name or in a branch, and one set at run time drops the entry; a menu's items drawn
in place (``menu_contents``) count as the parent's, under no title of their own; the shortest path comes first.
``test_native_learn_routes`` proves each route's path against the menu sources with its own reader, and
``test_blender_learn_routes`` proves the menus exist in the running app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_operator_spec, learn_menu_paths, learn_routes  # noqa: E402
import learn_menu_paths_source as source  # noqa: E402

SOURCES = '''
class VIEW3D_MT_select_edit_mesh(Menu):
    bl_label = "Select"

    def draw(self, context):
        layout = self.layout
        layout.menu("VIEW3D_MT_edit_mesh_select_loops")
        layout.menu("VIEW3D_MT_renamed", text="Another Text")
        layout.menu("VIEW3D_MT_edit_mesh_context_menu")
        layout.menu_contents("VIEW3D_MT_inline")
        layout.operator("mesh.loop_multi_select", text="Shortest")
        for name in ("A", "B"):
            layout.operator("mesh.dynamic_text", text=name)
        layout.operator("mesh.own_context", text="Other", text_ctxt=i18n_contexts.id_mesh)


class VIEW3D_MT_edit_mesh_select_loops(Menu):
    bl_label = "Select Loops"

    def draw(self, context):
        layout = self.layout
        layout.operator("mesh.loop_multi_select", text="Edge Loops").ring = False
        props = layout.operator("mesh.loop_multi_select", text="Edge Rings")
        props.ring = True
        if context.mode == 'EDIT_MESH':
            props = layout.operator("mesh.branch_set", text="Branch")
            props.extend = True
        layout.operator("mesh.run_time_value", text="Value").amount = context.scene.frame_current


class VIEW3D_MT_renamed(Menu):
    bl_label = "Renamed"

    def draw(self, context):
        self.layout.operator("mesh.under_renamed")


class VIEW3D_MT_edit_mesh_context_menu(Menu):
    bl_label = "Context"

    def draw(self, context):
        self.layout.operator("mesh.in_context_menu")


class VIEW3D_MT_inline(Menu):
    bl_label = ""

    def draw(self, context):
        self.layout.operator("mesh.drawn_in_place", text="In Place")
'''


class Table(unittest.TestCase):
    def test_the_checked_in_table_is_what_blenders_menus_build(self):
        self.assertEqual(learn_menu_paths.PATHS, source.build(),
                         "bl_ui changed: python3 tests/python/angelo_agent/learn_menu_paths_source.py --write")

    def test_every_header_menu_the_builder_starts_from_exists(self):
        found = source.menus()
        for name in sorted(action_operator_spec.ALLOWED):
            (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": name}}).routes
            for menu in source.roots(route.editor, route.mode, name):
                with self.subTest(operator=name, menu=menu):
                    self.assertIn(menu, found)

    def test_the_table_reaches_the_editors_and_modes_angelo_runs_operators_in(self):
        reached = set()
        for name, entries in learn_menu_paths.PATHS.items():
            (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": name}}).routes
            reached.add((route.editor, route.mode))
            for menus, item, properties in entries:
                self.assertTrue(menus and isinstance(item, str) and isinstance(properties, tuple), name)
        self.assertLessEqual({(learn_routes.VIEW_3D, learn_routes.OBJECT), (learn_routes.VIEW_3D, learn_routes.EDIT),
                              (learn_routes.UV_EDITOR, learn_routes.EDIT)}, reached)


class Rules(unittest.TestCase):
    """The builder's reading of menu sources, on sources written for the test."""

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        Path(folder.name, "space_view3d.py").write_text(textwrap.dedent(SOURCES), encoding="utf-8")
        with mock.patch.object(source, "BL_UI", Path(folder.name)):
            cls.found = source.menus()
        cls.chains = source.chains(cls.found, ("VIEW3D_MT_select_edit_mesh",))

    def entries(self, operator):
        return sorted((self.chains[menu], entry[1], tuple(sorted(entry[2].items())))
                      for menu in self.chains for entry in source._drawn(self.found, menu, "entries")
                      if entry is not None and entry[0] == operator and source._DYNAMIC not in entry[2].values())

    def test_a_path_goes_down_only_through_submenus_shown_under_their_own_title(self):
        self.assertEqual(set(self.chains), {"VIEW3D_MT_select_edit_mesh", "VIEW3D_MT_edit_mesh_select_loops"})
        self.assertEqual(self.entries("mesh.under_renamed"), [])        # Drawn as "Another Text", titled "Renamed".
        self.assertEqual(self.entries("mesh.in_context_menu"), [])      # Search leaves context menus out.

    def test_properties_are_read_on_the_call_on_a_name_and_in_a_branch(self):
        loops = ("VIEW3D_MT_select_edit_mesh", "VIEW3D_MT_edit_mesh_select_loops")
        self.assertEqual(self.entries("mesh.loop_multi_select"), [
            (("VIEW3D_MT_select_edit_mesh",), "Shortest", ()),
            (loops, "Edge Loops", (("ring", False),)),
            (loops, "Edge Rings", (("ring", True),))])
        self.assertEqual(self.entries("mesh.branch_set"), [(loops, "Branch", (("extend", True),))])

    def test_a_text_or_value_worked_out_at_run_time_gives_no_entry(self):
        for operator in ("mesh.dynamic_text", "mesh.own_context", "mesh.run_time_value"):
            with self.subTest(operator=operator):
                self.assertEqual(self.entries(operator), [])

    def test_items_drawn_in_place_are_the_parents(self):
        self.assertEqual(self.entries("mesh.drawn_in_place"), [(("VIEW3D_MT_select_edit_mesh",), "In Place", ())])


if __name__ == "__main__":
    unittest.main()
