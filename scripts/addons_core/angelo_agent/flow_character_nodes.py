# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The character-parts Generation Flow nodes: Split Sheet, Generate 3D per Part and Place Character.

They build on ``flow_nodes``' node classes and are registered by it. Draw functions read only what
``flow_host`` prepared (states, part summaries, preview icons), like every flow node.
"""

import json

from bpy.props import FloatProperty, IntProperty, StringProperty
from bpy.types import Node

from . import flow_nodes
from .flow_node_state import _PerUseState
from .flow_nodes import _FlowNode, _GeneratorNode, _models
from .ui_text import iface_, n_


def _part_grid(layout, tree, parts, chosen=None, columns=4):
    """Thumbnails of parts (crops, or each part's chosen model), labelled by part. Draw-safe: prepared previews only."""
    from . import flow_host
    from .flow_fanout import part_label
    grid = layout.grid_flow(columns=max(1, min(columns, len(parts))), even_columns=True, align=True)
    for part in parts:
        cell = grid.column(align=True)
        identity = (chosen or {}).get(part["id"]) if chosen is not None else part["asset"]
        icon = flow_host.preview_icon(identity) if identity else 0
        if icon:
            cell.template_icon(icon_value=icon, scale=2.5)
        else:
            cell.label(text="", icon='DOT')
        cell.label(text=part_label(part), translate=False)


class ANGELO_FlowSplitSheetNode(_FlowNode, Node):
    """Cut a parts sheet into one transparent part per filled column (free, local)"""
    bl_idname = "ANGELO_FlowSplitSheetNode"
    bl_label = "Split Sheet"
    bl_icon = 'UV_ISLANDSEL'
    angelo_type = "SPLIT_SHEET"
    rows: IntProperty(name="Rows", min=1, max=2, default=2, description="1: front views; 2: front and back views")
    slots_json: StringProperty(name="Slots", description="The sheet's columns, left to right",
                             translation_context="Angelo")  # Blender's own "Slots" means other things.
    split_json: StringProperty(options={'HIDDEN'})  # The stored split (flow_split_host) or {key, error}.

    def fields(self):
        try:
            slots = json.loads(self.slots_json or "[]")
        except ValueError:
            slots = []
        return {"rows": self.rows, "slots": slots}

    def draw_buttons(self, context, layout):
        state = self._status(layout)
        if state and state["state"] == "READY":
            _part_grid(layout, self.id_data, state["value"]["parts"])

    def draw_buttons_ext(self, context, layout):
        self.draw_buttons(context, layout)
        layout.prop(self, "rows")
        layout.prop(self, "slots_json")


class ANGELO_FlowGenerate3DEachNode(_PerUseState, _GeneratorNode, Node):
    """Generate one 3D model per part of a split parts sheet"""
    bl_idname = "ANGELO_FlowGenerate3DEachNode"
    bl_label = "Generate 3D per Part"
    bl_icon = 'MESH_MONKEY'
    angelo_type = "GENERATE_3D_EACH"
    prompt_hint = n_("Prompt (optional; each part's image is its reference)")
    model_choice: _models("3D")
    chosen_json: StringProperty(options={'HIDDEN'})   # {part: asset}
    last_key_json: StringProperty(options={'HIDDEN'})  # {part: input key}

    def _draw_options(self, layout):
        pass

    def fields(self):
        return dict(super().fields(), variants=1, review=False)

    def draw_buttons(self, context, layout):
        from . import flow_host
        layout.prop(self, "model_choice", text="")
        flow_nodes.connect_notice(layout, self.id_data, self.name, "3D", max(16, int(self.width / 8)))
        layout.prop(self, "prompt", text="", placeholder=iface_(self.prompt_hint), translate=False)
        self._status(layout)
        parts = flow_host.part_summary(self.id_data, self.name)
        if parts:
            _part_grid(layout, self.id_data, parts["parts"], chosen=parts["chosen"])
        row = layout.row(align=True)
        op = row.operator("angelo.flow_run", text="Run these parts", icon='PLAY')
        op.tree, op.node, op.scope = self.id_data.name, self.name, "NODE"
        op = row.operator("angelo.flow_run", text="From here")
        op.tree, op.node, op.scope = self.id_data.name, self.name, "DOWNSTREAM"


class ANGELO_FlowPlaceCharacterNode(_FlowNode, Node):
    """Put the parts together on the body as separate objects in the scene (undoable)"""
    bl_idname = "ANGELO_FlowPlaceCharacterNode"
    bl_label = "Place Character"
    bl_icon = 'ARMATURE_DATA'
    angelo_type = "PLACE_CHARACTER"
    height: FloatProperty(name="Height", translation_context="Angelo", min=0.1, max=10.0, default=1.7, unit='LENGTH',
                          description="The character's height in the scene")

    def fields(self):
        return {"height": round(self.height, 3)}

    def draw_buttons(self, context, layout):
        layout.prop(self, "height")
        state = self._status(layout)
        row = layout.row()
        row.enabled = bool(state and state["state"] == "READY")
        op = row.operator("angelo.flow_place_character", text="Place character", icon='IMPORT')
        op.tree, op.node = self.id_data.name, self.name


CLASSES = (ANGELO_FlowSplitSheetNode, ANGELO_FlowGenerate3DEachNode, ANGELO_FlowPlaceCharacterNode)
assert {cls.angelo_type: cls.bl_idname for cls in CLASSES} == flow_nodes.CHARACTER_IDNAMES


def register():
    import bpy
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    import bpy
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
