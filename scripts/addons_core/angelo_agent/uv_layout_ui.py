# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout by hand: the selected meshes unwrapped and packed for one texture set (doc/angelo/uv_layout.rst).

One operator with Blender's own dialog (no new panel), which also runs from its properties alone (EXEC_DEFAULT: the
agent's uv_layout operation through the shared typed-operation pattern): texture size, density, mip padding, unwrap,
stacking, grouping, packer and search, then ``uv_layout.run`` and a report of the receipt. Confirmed in its dialog, it
hands the layout to ``angelo.uv_layout_run``, which runs each pack as Pack Islands' own job (``uv_layout_job``): the
interface keeps drawing, the status bar shows progress and Esc stops it, putting the meshes back. Reached from the chat
bar's + menu and F3.
"""

import json

import bpy

from . import edit_tool_hold, uv_layout, uv_layout_job, uv_layout_ops
from .ui_text import iface_, n_

NAVIGATION = frozenset({"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM",
                        "MOUSEROTATE", "MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "NDOF_MOTION"})

TITLE = n_("UV Layout")
WHY = n_("Unwrap and pack the selection for one texture, at a texel density")
MARGIN_LOWERED = n_("Margin lowered to {mips} mip levels so {islands} islands fit; raise the texture size to keep "
                    "{wanted}")
TOO_LITTLE_ROOM = n_("Padding leaves the {islands} islands only {percent}% of the texture: lower Mip Levels Kept, "
                     "use a larger texture or set a Texel Density")


class ANGELO_OT_uv_layout(bpy.types.Operator):
    bl_idname = "angelo.uv_layout"
    bl_label = TITLE
    bl_description = WHY
    bl_options = {"REGISTER"}   # The Undo step is angelo.uv_layout_run's by hand, the program's for the agent.

    texture_size: bpy.props.EnumProperty(name="Texture", default="2048", items=[
        (str(2 ** p), "%d px" % 2 ** p, "") for p in range(8, 14)])
    use_density: bpy.props.BoolProperty(name="Texel Density", default=False,
                                        description="Keep this many pixels per metre, using more UDIM tiles if needed")
    density: bpy.props.FloatProperty(name="Pixels per Metre", default=1024.0, min=1.0, max=1e6)
    mips: bpy.props.IntProperty(name="Mip Levels Kept", default=4, min=0, max=13, options={"SKIP_SAVE"},
                                description="Islands stay apart down to this mip level (padding 2^n px)")
    unwrap: bpy.props.EnumProperty(name="Unwrap", default="KEEP", items=(
        ("KEEP", n_("Keep UVs"), ""), ("SEAMS", n_("From Seams"), ""), ("SMART", n_("Smart Project"), "")))
    stack: bpy.props.BoolProperty(name="Stack Identical Parts", default=False)
    mirrored: bpy.props.BoolProperty(name="Include Mirrored", default=False)
    group_by: bpy.props.EnumProperty(name="Tile per", default="NONE", items=(
        ("NONE", n_("Nothing"), ""), ("MATERIAL", n_("Material"), ""), ("OBJECT", n_("Object"), "")))
    search: bpy.props.IntProperty(name="Search", default=0, min=0, max=256,
                                  description="Extra layouts the packer tries; the tightest is kept")
    packer: bpy.props.EnumProperty(name="Packer", default="QUALITY", items=(
        ("QUALITY", n_("Quality"), n_("Islands packed by their shapes: the tightest layout")),
        ("FAST", n_("Fast"), n_("Islands packed by their boxes: far quicker, with more empty space"))))

    @classmethod
    def poll(cls, context):
        meshes = list(context.selected_objects) + [context.active_object]
        return any(o is not None and o.type == "MESH" for o in meshes) and context.mode in {"OBJECT", "EDIT_MESH"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type == "MESH"]
        active = context.active_object
        if active is not None and active.type == "MESH" and active not in objects:
            objects.insert(0, active)   # The staged target of the uv_layout operation, selected or not.
        spec = uv_layout_ops.to_layout({name: getattr(self, name) for name in uv_layout_ops.NAMES   # mips only when
                                        if name != "mips" or self.properties.is_property_set("mips")})   # set.
        if self.options.is_invoke:   # By hand: run as jobs, so the interface keeps working.
            started = bpy.ops.angelo.uv_layout_run("INVOKE_DEFAULT", spec=json.dumps(spec),
                                                   objects=json.dumps([o.name for o in objects]))
            # The runner is the modal one; this dialog is done either way.
            return {"FINISHED"} if started & {"RUNNING_MODAL", "FINISHED"} else {"CANCELLED"}
        was_object = context.mode == "OBJECT"
        if was_object:
            bpy.ops.object.mode_set(mode="EDIT")
        try:
            receipt = uv_layout.run(objects, spec)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            if was_object:
                bpy.ops.object.mode_set(mode="OBJECT")
        self.report(*_finish_report(receipt))
        return {"FINISHED"}


def _finish_report(receipt):
    """The finished layout as the person reads it: its summary, or, when padding left the islands too little room
    (doc/angelo/uv_layout.rst, "Too little room"), a warning naming the fixes. A banner in Director's top bar too."""
    room = receipt.get("room")
    if room and room["too_little"]:
        return {"WARNING"}, iface_(TOO_LITTLE_ROOM, islands=receipt["islands"], percent=round(room["share"] * 100, 1))
    lowered = receipt.get("margin_lowered")
    if lowered:   # Never lowered silently.
        return {"WARNING"}, iface_(MARGIN_LOWERED, mips=lowered["to"], islands=receipt["islands"],
                                   wanted=lowered["from"])
    return {"INFO"}, _summary(receipt)


def _summary(receipt):
    density = receipt["density_px_per_m"]
    return iface_("{islands} islands in {tiles} tile(s), {efficiency}% covered, {low}-{high} px/m",
                  islands=receipt["islands"], tiles=len(receipt["tiles"]),
                  efficiency=round(receipt["efficiency"] * 100), low=round(density["low"]), high=round(density["high"]))


class ANGELO_OT_uv_layout_run(bpy.types.Operator):
    """UV Layout's packs as Pack Islands' jobs, one tile at a time (uv_layout_job); Esc stops and puts the meshes
    back. Started by angelo.uv_layout's dialog."""
    bl_idname = "angelo.uv_layout_run"
    bl_label = TITLE
    bl_description = ""   # Internal: no button shows it, so no tooltip.
    # Its modal handler must run while a pack job locks the interface, so Esc reaches it; it only reads the job's
    # progress and stops it until the job ends.
    bl_options = {"UNDO", "INTERNAL", "LOCK_BYPASS"}

    spec: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    objects: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if context.window_manager.is_interface_locked:   # Never touch a mesh while a job locks the interface.
            self.report({"ERROR"}, iface_("Waits for a running job to finish first"))
            return {"CANCELLED"}
        names = json.loads(self.objects or "[]")
        objects = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
        self._was_object = context.mode == "OBJECT"
        self._faulted = False
        if self._was_object:
            bpy.ops.object.mode_set(mode="EDIT")
        try:
            self._layout = uv_layout_job.Layout(objects, json.loads(self.spec))
        except ValueError as error:
            self._end(context)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        self._show(context, 0.0)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            self._layout.stop(context.scene)
            return {"RUNNING_MODAL"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"} if event.type in NAVIGATION else {"RUNNING_MODAL"}
        if edit_tool_hold.running():   # The person's operation in another window: the layout's mesh waits for it.
            edit_tool_hold.note_waiting()
            return {"RUNNING_MODAL"}
        try:
            state, fraction = self._layout.advance(context.scene, context.window_manager.is_interface_locked)
        except Exception as error:   # Never left running half way, as the job runner ends one:
            reason = str(error) or type(error).__name__
            if not self._faulted:   # first the layout fails: the next advance puts the meshes back once no pack runs;
                self._faulted = True
                self._layout.fail(context.scene, reason)
                return {"RUNNING_MODAL"}
            if context.window_manager.is_interface_locked:   # again: at once, but never while a job locks the
                return {"RUNNING_MODAL"}                     # interface.
            try:
                self._layout.abandon(reason, restore=True)
            except Exception:
                pass
            state, fraction = "FAILED", 1.0
        if state == "PACKING":
            self._show(context, fraction)
            return {"RUNNING_MODAL"}
        context.window_manager.event_timer_remove(self._timer)
        self._end(context)
        if state == "DONE":
            self.report(*_finish_report(self._layout.receipt))
            return {"FINISHED"}
        if state == "STOPPED":
            self.report({"WARNING"}, iface_("UV Layout stopped: the meshes are as they were"))
        else:
            self.report({"ERROR"}, self._layout.error)
        return {"CANCELLED"}

    def _show(self, context, fraction):
        layout = getattr(self, "_layout", None)
        text = iface_("UV Layout: tile {done} of {tiles}, {percent}% (Esc to stop)",
                      done=min(layout.done + 1, layout.tiles) if layout else 1, tiles=layout.tiles if layout else 1,
                      percent=round(fraction * 100))
        context.workspace.status_text_set(text)

    def _end(self, context):
        context.workspace.status_text_set(None)
        if self._was_object and context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:   # Its poll failed (no active object meanwhile): nothing left to leave.
                pass


CLASSES = (ANGELO_OT_uv_layout, ANGELO_OT_uv_layout_run)


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "uv_layout", TITLE, WHY, glyph="uv_layout", order=47, section=n_("Model"),
        operator=ANGELO_OT_uv_layout.bl_idname, owner="uv_layout_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("uv_layout")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
