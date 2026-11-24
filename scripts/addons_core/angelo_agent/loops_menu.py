# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Loops menu: Blender's native LoopTools operators together, under LoopTools' names (doc/angelo/features.rst).

Blender 5.2 and 5.3 made the LoopTools add-on's tools native (``mesh.circularize``, ``mesh.flatten``,
``mesh.space_edge_loops_evenly``, ``mesh.relax_edge_loops``, ``mesh.curve_edge_loops_between_selected`` and, for
Gstretch, ``mesh.fit_edge_loops_to_annotation``), but drew them unlabelled as a group inside Mesh › Transform under
long names, and not in the edit-mode context menu where LoopTools put its menu. A LoopTools user searching for
"Relax" or "Gstretch" found an unfamiliar label or nothing (Blender 5.3 launch thread, 2026-11-10).

This module adds one menu, **Loops**, holding those operators and Bridge under the names LoopTools used, and shows it

* at the top of the edit-mode context menu (prepended, where LoopTools' own menu sat), and
* in the Edge menu (appended), a header menu, so F3's menu search finds every item as Edge › Loops › Relax.

Both are added from here, so ``bl_ui`` stays stock and the labels reach Angelo's catalogues (``locale/angelo``).
Nothing is removed from Mesh › Transform: stock Blender's places keep working. No operator is new; each runs as it
does from Blender's own menus. LoopTools' Loft has no native operator and is not offered.
"""

import bpy

from .ui_text import iface_, n_


class ANGELO_MT_loops(bpy.types.Menu):
    bl_idname = "ANGELO_MT_loops"   # Read by the draw callbacks below; Blender sets no attribute when it is left out.
    bl_label = "Loops"
    bl_description = "Shape and space edge loops: the built-in LoopTools tools"

    def draw(self, _context):
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'
        for idname, text, context in ITEMS:
            # Angelo's catalogue holds these in the default context (n_), or their own where the same words mean
            # something else (re-read of 70142a371dd, S-3), never Blender's "Operator" context of a button's text.
            layout.operator(idname, text=iface_(text, context), translate=False)


# LoopTools' names, alphabetical as LoopTools listed them, the native operator doing that job, and the catalogue
# context of a name whose words mean something else elsewhere ("Space" here spaces loops; elsewhere it is a space).
ITEMS = (
    ("mesh.bridge_edge_loops", n_("Bridge"), None),
    ("mesh.circularize", n_("Circle"), None),
    ("mesh.curve_edge_loops_between_selected", n_("Curve"), None),
    ("mesh.flatten", n_("Flatten"), None),
    ("mesh.fit_edge_loops_to_annotation", n_("Gstretch to Annotation"), None),
    ("mesh.relax_edge_loops", n_("Relax"), None),
    ("mesh.space_edge_loops_evenly", n_("Space", "Loops"), "Loops"),
)


def _draw_context_menu(self, _context):
    """Top of the edit-mode context menu, where LoopTools' menu was."""
    layout = self.layout
    layout.menu(ANGELO_MT_loops.bl_idname)
    layout.separator()


def _draw_edge_menu(self, _context):
    """The Edge header menu, so F3 finds each item by name (Edge › Loops › Relax)."""
    layout = self.layout
    layout.separator()
    layout.menu(ANGELO_MT_loops.bl_idname)


def register():
    bpy.utils.register_class(ANGELO_MT_loops)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.prepend(_draw_context_menu)
    bpy.types.VIEW3D_MT_edit_mesh_edges.append(_draw_edge_menu)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_edges.remove(_draw_edge_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(_draw_context_menu)
    bpy.utils.unregister_class(ANGELO_MT_loops)
