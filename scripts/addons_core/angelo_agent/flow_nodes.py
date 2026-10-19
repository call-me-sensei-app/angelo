# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Generation Flow node tree: Blender node, socket and tree types.

The node tree saved in the .blend is the flow's source of truth;
``flow_tree`` converts it to and from ``angelo.flow/1``. A tree with a recipe
output is a **recipe**: its exposed source nodes are inputs, and Subflow nodes
in other trees use it as one step. A Subflow node keeps its own chosen outputs,
input keys and captures for the recipe's steps (``inner``/``set_inner``).

Draw functions only read state that ``flow_host`` prepared on its timer; they
never evaluate the library, load images or touch providers.
"""

import json

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Node, NodeSocket, NodeTree

from .flow_spec import INPUTS, OUTPUTS, RESOLUTIONS
from .ui_text import iface_, n_

TREE = "ANGELO_GenerationFlowTree"
SOCKET_IDS = {"IMAGE": "ANGELO_FlowSocketImage", "TEXT": "ANGELO_FlowSocketText", "MODEL": "ANGELO_FlowSocketModel"}
SOCKET_KINDS = {idname: kind for kind, idname in SOCKET_IDS.items()}
# Socket names are marked here; Blender translates them where the node draws them.
SOCKET_LABELS = {"prompt": n_("Prompt", "Angelo"), "references": n_("References"), "reference": n_("Reference"), "model": n_("Model")}
OUTPUT_LABELS = {"IMAGE": n_("Image"), "TEXT": n_("Text"), "MODEL": n_("Model")}


def _interface_changed(self, context):
    """A recipe's interface changed: Subflow uses elsewhere resync on the next pump."""
    from . import flow_host
    flow_host.tree_changed(self.id_data)


class ANGELO_GenerationFlowTree(NodeTree):
    """Compose multi-step image and 3D generation"""
    bl_idname = TREE
    bl_label = "Generation Flow"
    bl_icon = 'IMAGE_DATA'
    bl_use_group_interface = False  # No node groups yet: hide Group Sockets and Make Group.

    angelo_flow_id: StringProperty(name="Flow ID", description="Identity recorded in the lineage of this flow's outputs")
    angelo_recipe_output: StringProperty(
        name="Recipe output", description="The generator whose chosen output a Subflow using this recipe passes on; "
        "empty when this flow isn't a recipe", update=_interface_changed)

    def update(self):
        from . import flow_host, flow_tree
        flow_tree.mark_invalid_links(self)
        flow_host.tree_changed(self)


def _socket_class(kind, color):
    class FlowSocket(NodeSocket):
        bl_idname = SOCKET_IDS[kind]
        bl_label = kind.title()

        def draw(self, context, layout, node, text):
            # The name (English, saved in the file) in Angelo's meaning: Blender would give "Prompt" its own.
            layout.label(text=iface_(self.name) if self.name else text, translate=False)

        @classmethod
        def draw_color_simple(cls):
            return color
    FlowSocket.__name__ = "ANGELO_FlowSocket" + kind.title()
    return FlowSocket


SOCKETS = (_socket_class("IMAGE", (0.11, 0.62, 0.46, 1.0)), _socket_class("TEXT", (0.50, 0.47, 0.87, 1.0)),
           _socket_class("MODEL", (0.85, 0.35, 0.19, 1.0)))


class _FlowNode:
    angelo_type = ""

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE

    def init(self, context):
        self.width = 200
        output = OUTPUTS[self.angelo_type]
        for name, (kind, limit) in INPUTS.get(self.angelo_type, {}).items():
            self.inputs.new(SOCKET_IDS[kind], SOCKET_LABELS[name], identifier=name, use_multi_input=limit > 1)
        if output:
            self.outputs.new(SOCKET_IDS[output], OUTPUT_LABELS[output], identifier="output")

    def fields(self):
        return {}

    def draw_label(self):
        # The node's name is the step name the agent and the run cards use.
        return self.name

    def _status(self, layout):
        from . import flow_host
        state = flow_host.node_state(self.id_data, self.name)
        if state:
            column = layout.column(align=True)
            column.alert = state["state"] in {"FAILED", "BLOCKED"}
            column.label(text=flow_host.LABELS.get(state["state"], state["state"].title()), icon=flow_host.state_icon(state["state"]))
            for line in flow_host.reason_lines(state.get("reason"), max(16, int(self.width / 7))):
                column.label(text=line)
        return state


class _SourceNode(_FlowNode):
    exposed: BoolProperty(name="Recipe input", description="When this flow is a recipe, Subflow nodes using it "
                          "get an input that replaces this node; unlinked, this node is the default",
                          update=_interface_changed)

    def draw_buttons_ext(self, context, layout):
        self.draw_buttons(context, layout)
        layout.prop(self, "exposed")


def _option_enum(key, choices, default):
    """An options-JSON field exposed as an enum, so the JSON stays the single record."""
    def get(self):
        value = json.loads(self.options_json or "{}").get(key, default)
        return choices.index(value) if value in choices else choices.index(default)

    def set(self, index):
        options = json.loads(self.options_json or "{}")
        options[key] = choices[index]
        self.options_json = json.dumps(options, sort_keys=True)
    return EnumProperty(name=key.title(), items=[(c, c, "") for c in choices], get=get, set=set)


# What a step lacks, said where it is drawn (flow_graph.connection_needs); "Connect a provider to generate" and
# "Connect in AI Providers" are Media Studio's own words for the same state. The button is angelo.flow_connect
# (flow_ui), whose tooltip says what the click does.
NO_ACCOUNT = {"IMAGE": n_("No image generation account yet"), "3D": n_("No 3D generation account yet")}
NOT_CONNECTED = n_("This step's account isn't connected")
RECIPE_NEEDS = n_("A recipe step needs a generation account")
TREE_NEEDS = n_("Connect a provider to generate")


def connect_notice(layout, tree, name=None, kind=None, width=36):
    """Say what account is missing and offer Connect in AI Providers, as Media Studio does; True when drawn.

    ``name`` is a step or Subflow use (None: any step of the tree), ``kind`` a generator's output kind and
    ``width`` about how many characters fit on a line (labels never wrap by themselves, translations run long).
    Reads only the needs ``flow_host`` prepared; a step that merely needs a model chosen gets nothing here.
    """
    from . import flow_host
    from .flow_graph import CONNECT, RECONNECT, need_for
    needs = (flow_host._cache.get(tree.name) or {}).get("needs") or {}
    need = need_for(needs, name) if name else next((n for n in (CONNECT, RECONNECT) if n in needs.values()), None)
    if need not in {CONNECT, RECONNECT}:
        return False
    text = (TREE_NEEDS if name is None else RECIPE_NEEDS if kind is None
            else NO_ACCOUNT[kind] if need == CONNECT else NOT_CONNECTED)
    column = layout.column(align=True)
    for index, line in enumerate(flow_host.reason_lines(iface_(text), width)):
        column.label(text=line, icon='ERROR' if index == 0 else 'BLANK1', translate=False)
    column.operator("angelo.flow_connect", text=iface_("Connect in AI Providers"), icon='PREFERENCES', translate=False)
    return True


_model_items = {"IMAGE": [], "3D": []}


def _models(kind):
    def items(self, context):
        from . import flow_host
        # A real item, not a heading (an empty identifier), so an unchosen picker reads "Choose a model".
        entries = [("NONE", n_("Choose a model"), n_("Pick a connected generation account and model"))]
        for entry in flow_host.catalog_entries(kind):
            identity = entry["connection"] + "|" + entry["id"]
            fields = dict(model=entry["name"], account=entry["account"])
            label = (iface_("{model} · {account}", **fields) if entry["ready"]
                     else iface_("{model} · {account} (not connected)", **fields))
            entries.append((identity, label, entry["account"]))
        _model_items[kind] = entries  # Blender requires enum strings to stay referenced.
        return entries

    def get(self):
        identity = self.connection_id + "|" + self.model
        for index, item in enumerate(_model_items[kind]):
            if item[0] == identity:
                return index
        return 0

    def set(self, index):
        items = _model_items[kind]
        if 0 < index < len(items):
            self.connection_id, self.model = items[index][0].split("|", 1)
    return EnumProperty(name="Model", items=items, get=get, set=set)


class _GeneratorNode(_FlowNode):
    connection_id: StringProperty(name="Account")
    model: StringProperty(name="Model ID")
    prompt: StringProperty(name="Prompt", translation_context="Angelo",
                           description="Linked Text nodes are added after this, in link order")
    options_json: StringProperty(name="Options", default="{}")
    variants: IntProperty(name="Variants", min=1, max=4, default=1,
                          description="How many alternatives to generate; you choose which one continues")
    review: BoolProperty(name="Stop for review", default=False,
                         description="Pause the run after this step so you can choose a variant")
    chosen: StringProperty(name="Chosen output")
    last_key: StringProperty(name="Last input key", options={'HIDDEN'})

    def init(self, context):
        super().init(context)
        self.width = 260

    def fields(self):
        try:
            options = json.loads(self.options_json or "{}")
        except ValueError:
            options = {}
        return {"connection_id": self.connection_id, "model": self.model, "prompt": self.prompt,
                "options": options, "variants": self.variants, "review": self.review}

    def draw_buttons(self, context, layout):
        layout.prop(self, "model_choice", text="")
        connect_notice(layout, self.id_data, self.name, "IMAGE" if self.angelo_type == "GENERATE_IMAGE" else "3D",
                       max(16, int(self.width / 8)))
        layout.prop(self, "prompt", text="", placeholder=iface_(self.prompt_hint), translate=False)
        self._draw_options(layout)
        row = layout.row(align=True)
        row.prop(self, "variants")
        row.prop(self, "review", text="Stop for review", toggle=True)
        state = self._status(layout)
        from . import flow_host
        if state and state.get("outputs"):
            grid = layout.grid_flow(columns=min(4, len(state["outputs"])), even_columns=True, align=True)
            for identity in state["outputs"][:4]:
                cell = grid.column(align=True)
                icon = flow_host.preview_icon(identity)
                if icon:
                    cell.template_icon(icon_value=icon, scale=3.0)
                chosen = identity == state.get("chosen")
                op = cell.operator("angelo.flow_choose", text=iface_("Chosen") if chosen else iface_("Use"), depress=chosen,
                                 translate=False)
                op.tree, op.node, op.asset_id = self.id_data.name, self.name, identity
        row = layout.row(align=True)
        op = row.operator("angelo.flow_run", text="Run this step", icon='PLAY')
        op.tree, op.node, op.scope = self.id_data.name, self.name, "NODE"
        op = row.operator("angelo.flow_run", text="From here")
        op.tree, op.node, op.scope = self.id_data.name, self.name, "DOWNSTREAM"

    def draw_buttons_ext(self, context, layout):
        self.draw_buttons(context, layout)
        layout.prop(self, "options_json")


class ANGELO_FlowGenerateImageNode(_GeneratorNode, Node):
    """Generate images with a Media Studio model"""
    bl_idname = "ANGELO_FlowGenerateImageNode"
    bl_label = "Generate Image"
    bl_icon = 'IMAGE_DATA'
    angelo_type = "GENERATE_IMAGE"
    prompt_hint = n_("Prompt (linked Text nodes are added after it)")
    model_choice: _models("IMAGE")
    aspect: _option_enum("aspect", ["1:1", "16:9", "9:16", "4:3", "3:4"], "1:1")
    resolution: _option_enum("resolution", ["1K", "2K", "4K"], "2K")

    def _draw_options(self, layout):
        row = layout.row(align=True)
        row.prop(self, "aspect", text="")
        row.prop(self, "resolution", text="")


class ANGELO_FlowGenerate3DNode(_GeneratorNode, Node):
    """Generate a 3D model with a Media Studio model"""
    bl_idname = "ANGELO_FlowGenerate3DNode"
    bl_label = "Generate 3D"
    bl_icon = 'MESH_MONKEY'
    angelo_type = "GENERATE_3D"
    prompt_hint = n_("Prompt (optional with a reference image)")
    model_choice: _models("3D")

    def _draw_options(self, layout):
        pass  # Polycount and texture live in Options (sidebar) and default per provider.


class ANGELO_FlowImageNode(_SourceNode, Node):
    """A reference image from the Media Studio library or a public URL"""
    bl_idname = "ANGELO_FlowImageNode"
    bl_label = "Image"
    bl_icon = 'IMAGE_REFERENCE'
    angelo_type = "IMAGE"
    asset_id: StringProperty(name="Library image")
    url: StringProperty(name="URL", description="A public HTTPS image")

    def fields(self):
        if self.asset_id:
            return {"source": {"kind": "ASSET", "id": self.asset_id}}
        if self.url:
            return {"source": {"kind": "URL", "id": self.url}}
        return {"source": None}

    def draw_buttons(self, context, layout):
        from . import flow_host
        icon = flow_host.preview_icon(self.asset_id) if self.asset_id else 0
        if icon:
            layout.template_icon(icon_value=icon, scale=4.0)
            layout.label(text=flow_host.asset_title(self.asset_id))
        elif self.url:
            layout.label(text=self.url[:60], icon='URL')
        op = layout.operator("angelo.flow_pick_image", text=iface_("Pick image…") if not (icon or self.url) else iface_("Change image…"),
                              icon='FILEBROWSER', translate=False)
        op.tree, op.node, op.input = self.id_data.name, self.name, ""


class ANGELO_FlowSceneCaptureNode(_SourceNode, Node):
    """Render the scene as a reference when a run starts"""
    bl_idname = "ANGELO_FlowSceneCaptureNode"
    bl_label = "Scene Capture"
    bl_icon = 'SCENE'
    angelo_type = "SCENE_CAPTURE"
    framing: EnumProperty(name="Framing", items=[("CAMERA", "Scene camera", ""), ("VIEWPORT", "Current view", "")])
    shading: EnumProperty(name="Shading", items=[("CLAY", "Clay", "Solid shading"), ("MATERIAL", "Material", "Material preview")])
    subject: EnumProperty(name="Subject", items=[("SCENE", "Whole scene", ""), ("SELECTION", "Selected objects", "")])
    resolution: EnumProperty(name="Resolution", items=[(str(r), f"{r} px", "") for r in RESOLUTIONS], default="1024")
    capture_asset: StringProperty(name="Last capture", options={'HIDDEN'})
    capture_hash: StringProperty(options={'HIDDEN'})

    def fields(self):
        return {"framing": self.framing, "shading": self.shading, "subject": self.subject, "resolution": int(self.resolution)}

    def draw_buttons(self, context, layout):
        layout.prop(self, "framing", text="")
        row = layout.row(align=True)
        row.prop(self, "shading", text="")
        row.prop(self, "subject", text="")
        from . import flow_host
        icon = flow_host.preview_icon(self.capture_asset) if self.capture_asset else 0
        if icon:
            layout.template_icon(icon_value=icon, scale=4.0)
        layout.label(text="Captured at run start", icon='INFO')


class ANGELO_FlowTextNode(_SourceNode, Node):
    """A prompt fragment; several can feed one generator, in order"""
    bl_idname = "ANGELO_FlowTextNode"
    bl_label = "Text"
    bl_icon = 'TEXT'
    angelo_type = "TEXT"
    text: StringProperty(name="Text")

    def fields(self):
        return {"text": self.text}

    def draw_buttons(self, context, layout):
        layout.prop(self, "text", text="", placeholder=iface_("Prompt fragment"), translate=False)


class ANGELO_FlowPlaceNode(_FlowNode, Node):
    """Import a generated 3D model into the scene (undoable)"""
    bl_idname = "ANGELO_FlowPlaceNode"
    bl_label = "Place in Scene"
    bl_icon = 'IMPORT'
    angelo_type = "PLACE_IN_SCENE"

    def draw_buttons(self, context, layout):
        state = self._status(layout)
        row = layout.row()
        row.enabled = bool(state and state["state"] == "READY")
        op = row.operator("angelo.flow_place", text="Place in scene", icon='IMPORT')
        op.tree, op.node = self.id_data.name, self.name


def _is_recipe(self, tree):
    return tree.bl_idname == TREE and bool(tree.angelo_recipe_output) and tree != self.id_data


def _recipe_changed(self, context):
    from . import flow_tree
    flow_tree.sync_sockets(self)


class ANGELO_FlowSubflowNode(_FlowNode, Node):
    """Run a recipe flow as one step; its inputs and output come from the recipe"""
    bl_idname = "ANGELO_FlowSubflowNode"
    bl_label = "Subflow"
    bl_icon = 'NODETREE'
    angelo_type = "SUBFLOW"
    recipe: PointerProperty(name="Recipe", type=NodeTree, poll=_is_recipe, update=_recipe_changed)
    # This use's own state for the recipe's steps, by inner step name.
    chosen_json: StringProperty(options={'HIDDEN'})
    last_key_json: StringProperty(options={'HIDDEN'})
    capture_asset_json: StringProperty(options={'HIDDEN'})
    capture_hash_json: StringProperty(options={'HIDDEN'})

    def init(self, context):
        super().init(context)
        self.width = 240

    def fields(self):
        return {"recipe": self.recipe.name if self.recipe else ""}

    def inner(self, field):
        """{step: value} of one per-use field: chosen, last_key, capture_asset or capture_hash."""
        try:
            value = json.loads(getattr(self, field + "_json") or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def set_inner(self, field, step, value):
        values = self.inner(field)
        if value:
            values[step] = value
        else:
            values.pop(step, None)
        text = json.dumps(values, sort_keys=True) if values else ""
        if getattr(self, field + "_json") != text:
            setattr(self, field + "_json", text)

    def draw_buttons(self, context, layout):
        from . import flow_host
        row = layout.row(align=True)
        row.prop(self, "recipe", text="")
        if self.recipe:
            op = row.operator("angelo.flow_open_recipe", text="", icon='NODETREE')
            op.recipe = self.recipe.name
        self._status(layout)
        connect_notice(layout, self.id_data, self.name, width=max(16, int(self.width / 8)))
        summary = flow_host.subflow_summary(self.id_data, self.name)
        output = summary.get("output") if summary else None
        if output and output.get("outputs"):
            grid = layout.grid_flow(columns=min(4, len(output["outputs"])), even_columns=True, align=True)
            for identity in output["outputs"][:4]:
                cell = grid.column(align=True)
                icon = flow_host.preview_icon(identity)
                if icon:
                    cell.template_icon(icon_value=icon, scale=3.0)
                chosen = identity == output.get("chosen")
                op = cell.operator("angelo.flow_choose", text=iface_("Chosen") if chosen else iface_("Use"), depress=chosen,
                                 translate=False)
                op.tree, op.node, op.asset_id = self.id_data.name, summary["output_step"], identity
        row = layout.row(align=True)
        op = row.operator("angelo.flow_run", text="Run subflow", icon='PLAY')
        op.tree, op.node, op.scope = self.id_data.name, self.name, "NODE"
        op = row.operator("angelo.flow_run", text="From here")
        op.tree, op.node, op.scope = self.id_data.name, self.name, "DOWNSTREAM"


NODES = (ANGELO_FlowImageNode, ANGELO_FlowSceneCaptureNode, ANGELO_FlowTextNode,
         ANGELO_FlowGenerateImageNode, ANGELO_FlowGenerate3DNode, ANGELO_FlowPlaceNode, ANGELO_FlowSubflowNode)
IDNAME_BY_TYPE = {cls.angelo_type: cls.bl_idname for cls in NODES}
TYPE_BY_IDNAME = {cls.bl_idname: cls.angelo_type for cls in NODES}


def draw_add_menu(self, context):
    if getattr(context.space_data, "tree_type", "") != TREE:
        return
    layout = self.layout
    for idname in (ANGELO_FlowImageNode.bl_idname, ANGELO_FlowSceneCaptureNode.bl_idname, ANGELO_FlowTextNode.bl_idname):
        self.node_operator(layout, idname)
    layout.separator()
    for idname in (ANGELO_FlowGenerateImageNode.bl_idname, ANGELO_FlowGenerate3DNode.bl_idname):
        self.node_operator(layout, idname)
    layout.separator()
    self.node_operator(layout, ANGELO_FlowPlaceNode.bl_idname)
    self.node_operator(layout, ANGELO_FlowSubflowNode.bl_idname)


CLASSES = (ANGELO_GenerationFlowTree,) + SOCKETS + NODES


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.NODE_MT_add.append(draw_add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(draw_add_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
