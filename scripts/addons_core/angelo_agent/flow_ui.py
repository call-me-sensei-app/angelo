# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Node Editor operators and sidebar for Generation Flows.

A manual run asks for consent in a dialog that lists every paid generation,
like Media Studio's Generate button: confirming it is the approval. Unticking
a step leaves out the steps after it; a review stop defers them. The agent's
runs use the chat approval card instead (``flow_tools``); both start the same
``flow_runner.FlowRun``.
"""

import uuid

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import flow_host, flow_nodes, flow_prices, flow_tree
from .flow_approvals import price_text
from .flow_setup import step_label
from .flow_graph import check_selection, connect_steps, connection_needs, plan, scope_nodes, select
from .flow_spec import FlowError, REFERENCE_INPUT
from .ui_text import iface_, n_, plural_, rpt_

_dialog = {}  # The open run dialog's plan and its one-time approval token, re-checked on confirm.
connect_notices = []  # Steps each "Connect a provider to generate" popup named, newest last: fixtures assert on them.
NEEDS_ACCOUNT = n_("Nothing ran. These steps need a connected generation account:")
LEFT_OUT = n_("Left out until a generation account is connected: {steps}")


def show_connect_notice(context, lines):
    """A run attempt that can't start for want of an account: say which steps, and offer AI Providers."""
    labels = [step_label(line["label"]) for line in lines]
    connect_notices.append(labels)
    del connect_notices[:-8]

    def draw(menu, _context):
        layout = menu.layout
        layout.label(text=iface_(NEEDS_ACCOUNT), translate=False)
        for label in labels[:6]:
            layout.label(text=label, icon='DOT', translate=False)
        layout.separator()
        layout.operator("angelo.flow_connect", text=iface_("Connect in AI Providers"), icon='PREFERENCES', translate=False)
    context.window_manager.popup_menu(draw, title=iface_("Connect a provider to generate"), icon='ERROR')


class ANGELO_OT_flow_connect(Operator):
    """Open AI Providers to connect an image or 3D generation account"""
    bl_idname = "angelo.flow_connect"
    bl_label = "Connect in AI Providers"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        try:
            return bpy.ops.angelo.settings('INVOKE_DEFAULT')
        except RuntimeError as error:  # Preferences couldn't open; angelo.settings says why.
            self.report({'ERROR'}, str(error).partition(": ")[2] or str(error))
            return {'CANCELLED'}


def _tree(name):
    tree = bpy.data.node_groups.get(name)
    if tree is None or tree.bl_idname != flow_nodes.TREE:
        raise FlowError(rpt_("This Generation Flow no longer exists"))
    return tree


def _accounts():
    return {(e["connection"], e["id"]): e["name"] + " · " + e["account"]
            for kind in ("IMAGE", "3D") for e in flow_host.catalog_entries(kind)}


def build_plan(tree, window, scope, node):
    """Capture the scene inputs a run needs, then plan it. Shared by the dialog and the agent."""
    flow, _ = flow_host.evaluate_tree(tree)
    flow_host.capture_inputs(tree, window, flow_host.needed_captures(flow, scope_nodes(flow, scope, node or None)))
    flow, states = flow_host.evaluate_tree(tree)
    return flow, plan(flow, states, scope=scope, node=node or None, price=flow_prices.price, accounts=_accounts())


class ANGELO_FlowRunLine(PropertyGroup):
    node: StringProperty()
    label: StringProperty()
    detail: StringProperty()
    include: BoolProperty(name="Include", translation_context="Angelo", default=True)
    stop: BoolProperty(name="Stop for review", description="Pause after this step so you can choose a variant")


class ANGELO_OT_flow_run(Operator):
    """Run this flow's stale or missing steps after you confirm every paid generation"""
    bl_idname = "angelo.flow_run"
    bl_label = "Run Generation Flow"
    # Not REGISTER: Repeat Last and Repeat History re-ran execute with the last properties and no dialog, starting
    # paid generations, the steps the person unticked included (1.0.5 break-it review). A run starts only from the
    # dialog that showed it: invoke issues a one-time approval token, and execute starts nothing without it.
    bl_options = {'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    approval: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})
    node: StringProperty(options={'SKIP_SAVE'})
    scope: EnumProperty(items=[(s, s.title(), "") for s in ("ALL", "NODE", "DOWNSTREAM", "UPSTREAM")], options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            tree = _tree(self.tree or context.space_data.edit_tree.name)
            self.tree = tree.name
            flow, run_plan = build_plan(tree, context.window, self.scope, self.node)
        except (FlowError, ValueError, RuntimeError, AttributeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        needs_account = connect_steps(run_plan, connection_needs(flow, flow_host.catalog, connected=flow_host.connected_kinds()))
        lines = context.window_manager.angelo_flow_run_lines
        lines.clear()
        for line in run_plan["lines"]:
            if line["action"] != "GENERATE":
                continue
            item = lines.add()
            item.node, item.label = line["node"], step_label(line["label"])
            each = line["price_each"]
            item.detail = iface_("{count} × {account} · {cost} · {reason}", count=line["count"], account=line["account"],
                                 cost=f"≈ ${each * line['count']:.2f}" if each is not None else iface_("price unknown"),
                                 reason=line["reason"])
            item.stop = line["review"]
        if not lines and needs_account:
            show_connect_notice(context, needs_account)
            return {'CANCELLED'}
        if not lines:
            blocked = [l for l in run_plan["lines"] if l["action"] == "BLOCKED"]
            self.report({'INFO'}, rpt_("Nothing to generate. {blocked}", blocked="; ".join(f"{l['node']}: {l['reason']}" for l in blocked[:3]))
                        if blocked else rpt_("Nothing to generate: every step in this run is up to date"))
            return {'CANCELLED'}
        self.approval = uuid.uuid4().hex
        _dialog.clear()
        _dialog.update(tree=tree.name, flow=flow, plan=run_plan, left_out=[step_label(line["label"]) for line in needs_account],
                       approval=self.approval)
        return context.window_manager.invoke_props_dialog(self, width=520, title=iface_("Run Generation Flow"),
                                                          confirm_text=iface_("Run"))

    def _selection(self, context):
        lines = context.window_manager.angelo_flow_run_lines
        return select(_dialog["plan"], _dialog["flow"], excluded=[l.node for l in lines if not l.include],
                      stops={l.node: l.stop for l in lines})

    def draw(self, context):
        layout = self.layout
        selection = self._selection(context)
        now = {l["node"] for l in selection["now"]}
        later = {l["node"] for l in selection["later"]}
        for item in context.window_manager.angelo_flow_run_lines:
            box = layout.box()
            row = box.row()
            row.prop(item, "include", text=item.label, translate=False)
            row.prop(item, "stop", text="Stop for review", toggle=True)
            detail = (item.detail if item.node in now else
                      iface_("{detail} (after review: next card)", detail=item.detail) if item.node in later else
                      iface_("{detail} (left out)", detail=item.detail))
            box.label(text=detail, translate=False)
        if _dialog.get("left_out"):
            box = layout.box()
            box.label(text=iface_(LEFT_OUT, steps=", ".join(_dialog["left_out"][:6])), icon='ERROR', translate=False)
            box.operator("angelo.flow_connect", text=iface_("Connect in AI Providers"), icon='PREFERENCES', translate=False)
        layout.separator()
        total = price_text(selection["total"], selection["unknown_prices"])
        layout.label(text=plural_(selection["count"], "Runs {count} paid generation now · {total}",
                                  "Runs {count} paid generations now · {total}", total=total), icon='FUND', translate=False)
        layout.label(text="Billed to these API accounts, not a coding subscription. No automatic retries.")
        layout.label(text="Unchanged steps are reused for free. Stop only ends local monitoring.")

    def execute(self, context):
        if not self.approval or self.approval != _dialog.pop("approval", None):
            # Not the confirm of the dialog that showed this plan (a repeat, a script, a dialog already used).
            context.window_manager.angelo_flow_run_lines.clear()
            self.report({'WARNING'}, rpt_("Nothing ran. Run the flow again to review and confirm its generations"))
            return {'CANCELLED'}
        try:
            tree = _tree(self.tree)
            flow, current = build_plan(tree, context.window, self.scope, self.node)
            if current["digest"] != _dialog.get("plan", {}).get("digest"):
                raise FlowError(rpt_("The flow changed while this dialog was open; nothing ran. Run it again to review the new plan"))
            selection = self._selection(context)
            check_selection(selection)
        except (FlowError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            _dialog.clear()
            return {'CANCELLED'}
        finally:
            context.window_manager.angelo_flow_run_lines.clear()
        _dialog.clear()
        flow_host.start_run(tree, selection, owner="ui")
        self.report({'INFO'}, plural_(selection["count"], "Started {count} generation", "Started {count} generations"))
        return {'FINISHED'}


class ANGELO_OT_flow_choose(Operator):
    """Pass this output to the steps after it"""
    bl_idname = "angelo.flow_choose"
    bl_label = "Choose Output"
    bl_options = {'UNDO', 'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})
    asset_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            choose(_tree(self.tree), self.node, self.asset_id)
        except FlowError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def choose(tree, name, asset_id):
    """Set a generator step's chosen output (a Subflow's inner step: on that use only)."""
    flow_host.set_chosen(tree, name, asset_id)


def _library_images(self, context):
    images = sorted((a for a in flow_host.library().assets.values()
                     if a.get("kind") == "IMAGE" and a.get("status") == "ready" and a.get("files")),
                    key=lambda a: a.get("created", 0), reverse=True)[:200]
    items = [("FILE", n_("From a file…"), n_("Import an image file into the library"), 'FILEBROWSER', 0)]
    items += [(a["id"], (a.get("prompt") or iface_("Image"))[:60], a.get("model", ""), 'IMAGE_DATA', i + 1) for i, a in enumerate(images)]
    _library_images.cache = items  # Blender requires enum strings to stay referenced.
    return items


_library_images.cache = []


def _use_image(tree, name, socket_name, asset_id):
    """Point an Image node at an asset, or wire a new Image node into a generator's reference input."""
    node = tree.nodes.get(name)
    if node is None:
        raise FlowError(rpt_("That node no longer exists"))
    if node.bl_idname == flow_nodes.ANGELO_FlowImageNode.bl_idname and not socket_name:
        node.asset_id, node.url = asset_id, ""
        return node
    kind = flow_nodes.TYPE_BY_IDNAME.get(node.bl_idname)
    socket = next((s for s in node.inputs if s.identifier == (socket_name or REFERENCE_INPUT.get(kind, ""))), None)
    if socket is None:
        raise FlowError(rpt_("This node has no reference input"))
    image = tree.nodes.new(flow_nodes.ANGELO_FlowImageNode.bl_idname)
    # Below and left of the step it feeds, stacked per existing link, so it
    # doesn't land on the nodes of the previous column.
    image.location = (node.location.x - 240, node.location.y - 320 - 200 * len(socket.links))
    image.asset_id = asset_id
    tree.links.new(image.outputs["output"], socket)
    return image


class ANGELO_OT_flow_pick_image(Operator):
    """Pick a reference image from the Media Studio library or a file. Picking adds an Image node wired into the input"""
    bl_idname = "angelo.flow_pick_image"
    bl_label = "Pick Image"
    bl_options = {'UNDO', 'INTERNAL'}
    bl_property = "choice"
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})
    input: StringProperty(options={'SKIP_SAVE'})
    choice: EnumProperty(items=_library_images)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.choice == "FILE":
            bpy.ops.angelo.flow_pick_image_file('INVOKE_DEFAULT', tree=self.tree, node=self.node, input=self.input)
            return {'FINISHED'}
        try:
            _use_image(_tree(self.tree), self.node, self.input, self.choice)
        except FlowError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_flow_pick_image_file(Operator):
    """Import an image file into the Media Studio library and use it as a reference"""
    bl_idname = "angelo.flow_pick_image_file"
    bl_label = "Use Image File"
    bl_options = {'UNDO', 'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})
    input: StringProperty(options={'SKIP_SAVE'})
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            asset = flow_host.library().import_image(self.filepath)
            _use_image(_tree(self.tree), self.node, self.input, asset["id"])
        except (FlowError, ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_flow_place(Operator):
    """Import the chosen 3D output into the scene (undoable)"""
    bl_idname = "angelo.flow_place"
    bl_label = "Place in Scene"
    bl_options = {'UNDO', 'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from .studio_host import _import_model
        try:
            tree = _tree(self.tree)
            state = flow_host.refresh(tree)["states"].get(self.node) or {}
            asset = flow_host.library().assets.get(state.get("value") or "")
            if state.get("state") != "READY" or not asset:
                raise FlowError(rpt_("Run the Generate 3D step first; there's no chosen model to place"))
            path = flow_host.library().checked_file(asset, asset["files"][0])
            result = _import_model(context.window, path)
        except (FlowError, ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if not result.get("success"):
            self.report({'ERROR'}, result.get("error", rpt_("Nothing was imported")))
            return {'CANCELLED'}
        self.report({'INFO'}, plural_(result["object_count"], "Placed {count} object", "Placed {count} objects"))
        return {'FINISHED'}


class ANGELO_OT_flow_stop(Operator):
    """Stop this flow's runs. Submitted provider work may still finish and be billed"""
    bl_idname = "angelo.flow_stop"
    bl_label = "Stop Flow Runs"
    bl_options = {'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        for run in flow_host.active_runs(self.tree):
            flow_host.stop_run(run.id)
        return {'FINISHED'}


class ANGELO_OT_flow_new(Operator):
    """Create a Generation Flow and show it in this Node Editor"""
    bl_idname = "angelo.flow_new"
    bl_label = "New Generation Flow"
    bl_options = {'UNDO'}

    def execute(self, context):
        tree = bpy.data.node_groups.new("Generation Flow", flow_nodes.TREE)  # i18n: datablock name the agent and saved files refer to
        flow_tree.flow_id(tree)
        space = context.space_data
        if space and space.type == 'NODE_EDITOR':
            space.tree_type = flow_nodes.TREE
            space.node_tree = tree
        return {'FINISHED'}


class ANGELO_PT_flow(Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Flow"
    bl_label = "Generation Flow"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == flow_nodes.TREE

    def draw(self, context):
        layout = self.layout
        tree = context.space_data.edit_tree
        if tree is None:
            layout.operator("angelo.flow_new", icon='ADD')
            return
        entry = flow_host._cache.get(tree.name)
        if entry and entry["error"]:
            layout.label(text=entry["error"], icon='ERROR')
        flow_nodes.connect_notice(layout, tree, width=max(16, int(context.region.width / (8 * context.preferences.system.ui_scale))))
        runs = flow_host.active_runs(tree.name)
        if runs:
            started = sum(len(s["assets"]) for run in runs for s in run.steps.values())
            layout.label(text=plural_(started, "Running · {count} generation started", "Running · {count} generations started"),
                         icon='SORTTIME', translate=False)
            layout.operator("angelo.flow_stop", icon='CANCEL').tree = tree.name
        op = layout.operator("angelo.flow_run", text="Run flow", icon='PLAY')
        op.tree, op.scope = tree.name, "ALL"
        layout.label(text="Unchanged steps are reused; you confirm every paid generation.")


CLASSES = (ANGELO_FlowRunLine, ANGELO_OT_flow_connect, ANGELO_OT_flow_run, ANGELO_OT_flow_choose, ANGELO_OT_flow_pick_image,
           ANGELO_OT_flow_pick_image_file, ANGELO_OT_flow_place, ANGELO_OT_flow_stop, ANGELO_OT_flow_new, ANGELO_PT_flow)


def register():
    from . import flow_drop, flow_recipe_ui
    flow_host.register()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.angelo_flow_run_lines = CollectionProperty(type=ANGELO_FlowRunLine)
    flow_recipe_ui.register()
    flow_drop.register()


def unregister():
    from . import flow_drop, flow_recipe_ui
    flow_drop.unregister()
    flow_recipe_ui.unregister()
    del bpy.types.WindowManager.angelo_flow_run_lines
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    flow_host.unregister()
    _dialog.clear()
