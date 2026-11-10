# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's web export and live preview: File > Export > Web (.glb, Three.js) and Live Web Preview.

Both call what the agent's ``angelo_web_export`` calls (``web_export``, ``web_preview``), with the same defaults,
so a person and the agent get the same file. The full report (what doesn't carry over, with each fix) is on the
preview page; the export reports its size budget in the status bar. While the World's picture is still being made
the export waits for it as a modal operator: Blender keeps drawing and answering input, the status bar says so and
Esc cancels (``web_export.pending_export``); an export written without its World says so last, as a warning.
"""

import bpy
from bpy_extras.io_utils import ExportHelper

from . import input_policy, web_export, web_export_wait, web_preview
from .ui_text import iface_, n_, rpt_

MESH_ITEMS = (("AUTO", n_("Automatic"), n_("Meshopt when this build has it, else Draco, else uncompressed")),
              ("MESHOPT", n_("Meshopt"), n_("Small and fast to decode in Three.js (EXT_meshopt_compression)")),
              ("DRACO", n_("Draco"), n_("Smallest geometry; Three.js needs its Draco decoder")),
              ("NONE", n_("None"), n_("Uncompressed geometry")))
TEXTURE_ITEMS = (("AUTO", n_("Automatic"), n_("KTX2 when gltfpack is set up, else WebP")),
                 ("WEBP", n_("WebP"), n_("Small images every current browser reads")),
                 ("KTX2", "KTX2", n_("GPU-compressed textures through gltfpack (Basis Universal)")),
                 ("KEEP", n_("Keep"), n_("The images' own PNG and JPEG files")))
EXPORTED = n_("Exported {size} for the web: {triangles} triangles, {calls} draw calls, {textures} textures, "
              "{findings} findings (Live Web Preview lists them)")
FAILED = n_("The web export failed: {reason}")
WAITING = n_("Web export: making the World's picture ({seconds} s). Esc cancels")
CANCELLED = n_("Web export cancelled; nothing was written")
STEP_SECONDS = 0.1
STARTED = n_("Web preview is live in your browser")
STOPPED = n_("Web preview stopped")


def _size(count):
    for unit in ("B", "KB", "MB"):
        if count < 1024 or unit == "MB":
            return ("%d %s" % (count, unit)) if unit == "B" else ("%.1f %s" % (count, unit))
        count /= 1024.0


class ANGELO_OT_web_export(bpy.types.Operator, ExportHelper):
    bl_idname = "angelo.web_export"
    bl_label = "Export for Web"
    bl_description = ("Export a web-ready glTF for Three.js: compressed meshes and textures, merged draw calls "
                      "where safe, and a size budget")
    bl_options = {'PRESET'} | input_policy.modal_options(bl_idname)   # Auto-save never waits for its wait.
    filename_ext = ".glb"
    filter_glob: bpy.props.StringProperty(default="*.glb", options={'HIDDEN'})
    meshes: bpy.props.EnumProperty(name="Meshes", items=MESH_ITEMS, default="AUTO")
    textures: bpy.props.EnumProperty(name="Textures", items=TEXTURE_ITEMS, default="AUTO")
    merge: bpy.props.BoolProperty(name="Merge Draw Calls", default=True,
                                  description="Join static objects that share materials (they lose their own names)")
    selection_only: bpy.props.BoolProperty(name="Selected Only", default=False)
    baked_materials: bpy.props.BoolProperty(
        name="Baked Materials", default=True,
        description="Objects with a current baked material (Bake Maps) export with it; your scene keeps its own")

    def execute(self, context):
        request = {"meshes": self.meshes, "textures": self.textures, "merge": self.merge,
                   "selection_only": self.selection_only, "baked_materials": self.baked_materials}
        self._pending = web_export.pending_export(context.window, context.scene, context.view_layer, self.filepath,
                                                  request)
        self._timer = self._workspace = None
        if context.window is None:   # Background: no window to keep responsive, so the export waits here.
            while (outcome := self._step(context)) == {'RUNNING_MODAL'}:
                self._pending.job.done.wait(STEP_SECONDS)
            self._end()
            return outcome
        outcome = self._step(context)
        if outcome == {'RUNNING_MODAL'}:
            manager = context.window_manager
            self._timer = manager.event_timer_add(STEP_SECONDS, window=context.window)
            manager.modal_handler_add(self)
        return outcome

    def modal(self, context, event):
        if event.type != 'TIMER':   # Every event passes on; Esc also cancels the wait (not while playback runs).
            playing = context.screen is not None and context.screen.is_animation_playing
            if event.type == 'ESC' and event.value == 'PRESS' and not playing:
                self._pending.cancel()
                self._end()
                self.report({'INFO'}, rpt_(CANCELLED))
                return {'CANCELLED', 'PASS_THROUGH'}
            return {'PASS_THROUGH'}
        outcome = {'CANCELLED'}
        try:   # A step finds nothing to do until the picture's job is done.
            outcome = self._step(context)
        finally:
            if outcome != {'RUNNING_MODAL'}:
                self._end()
        return outcome

    def cancel(self, context):
        """Blender ends the operator (a file loads, the window closes, or the file browser was cancelled)."""
        if getattr(self, "_pending", None) is not None:
            self._pending.cancel()
        self._end()

    def _end(self):
        timer, self._timer = getattr(self, "_timer", None), None
        if timer is not None:
            bpy.context.window_manager.event_timer_remove(timer)
        workspace, self._workspace = getattr(self, "_workspace", None), None
        if workspace is not None:
            try:
                workspace.status_text_set(None)   # The workspace it was set on, wherever the person is now.
            except ReferenceError:
                pass

    def _step(self, context):
        try:
            report = self._pending.step()
        except Exception as error:  # Said in the status bar; the operator ends cleanly whatever the export raised.
            self.report({'ERROR'}, rpt_(FAILED, reason=(str(error) or type(error).__name__)[:200]))
            return {'CANCELLED'}
        if report is None:
            if getattr(self, "_workspace", None) is None:
                self._workspace = context.workspace
            if self._workspace is not None:
                waited = round(self._pending.clock() - self._pending.started)
                self._workspace.status_text_set(iface_(WAITING, seconds=waited))
            return {'RUNNING_MODAL'}
        budget = report["budget"]
        self.report({'INFO'}, rpt_(EXPORTED, size=_size(budget["bytes"]), triangles=budget["triangles"],
                                   calls=budget["draw_calls"], textures=budget["textures"]["count"],
                                   findings=len(report["findings"])))
        for item in web_export_wait.left_out(report):   # Last, so the status bar shows it: the World is missing.
            self.report({'WARNING'}, rpt_(item["message"], **item["params"]))
        return {'FINISHED'}


class ANGELO_OT_web_preview(bpy.types.Operator):
    bl_idname = "angelo.web_preview"
    bl_label = "Live Web Preview"
    bl_description = ("Show the scene in your browser as Three.js renders it, updated after every change, with "
                      "what doesn't carry over. Runs on this computer only")

    stop: bpy.props.BoolProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        if self.stop:
            web_preview.stop()
            self.report({'INFO'}, rpt_(STOPPED))
            return {'FINISHED'}
        request = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}
        web_preview.start(context.window, request)
        self.report({'INFO'}, rpt_(STARTED))
        return {'FINISHED'}


def approval_message(name, args):
    """The approval card of the agent's web export and preview calls (``approvals.approval_message``)."""
    if name == "angelo_web_export":
        return {"title": iface_("Export {output} for the web", output=args["output"]),
                "body": iface_("One .glb for Three.js in the file workspace: {meshes} meshes, {textures} textures, "
                               "draw calls merged: {merge}.", meshes=args["meshes"], textures=args["textures"],
                               merge=iface_("Yes") if args["merge"] else iface_("No")),
                "warning": iface_("Writes under a free name, never over a file. The scene doesn't change.")}
    if args.get("action") == "STOP":
        return {"title": iface_("Stop the live web preview"), "body": "", "warning": ""}
    return {"title": iface_("Open the live web preview"),
            "body": iface_("Shows the scene in your browser as Three.js renders it, re-exported after every change."),
            "warning": iface_("A page on this computer only (127.0.0.1). It stops when you stop it or quit Angelo.")}


def draw_menu(self, context):
    layout = self.layout
    layout.operator(ANGELO_OT_web_export.bl_idname, text=iface_("Web (.glb, Three.js)"), translate=False)
    if web_preview.running():
        layout.operator(ANGELO_OT_web_preview.bl_idname, text=iface_("Stop Web Preview"), translate=False).stop = True
    else:
        layout.operator(ANGELO_OT_web_preview.bl_idname)


CLASSES = (ANGELO_OT_web_export, ANGELO_OT_web_preview)


def _destinations():
    """The chat bar's + menu rows (chat_destinations): Angelo's main navigation, in Director and Manual."""
    from . import chat_destinations as cd
    return (cd.Feature("web_export", n_("Web export"), n_("A web-ready .glb for Three.js, with its size budget"),
                       glyph="web", order=50, section=n_("Share"), operator=ANGELO_OT_web_export.bl_idname,
                       owner="web_export_ui"),
            cd.Feature("web_preview", n_("Live browser preview"),
                       n_("The scene in your browser as Three.js draws it, updated as you work"),
                       glyph="web", order=51, section=n_("Share"), operator=ANGELO_OT_web_preview.bl_idname,
                       owner="web_export_ui"))


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for feature in _destinations():
        chat_destinations.register(feature)
    bpy.types.TOPBAR_MT_file_export.append(draw_menu)
    web_preview.register()


def unregister():
    from . import chat_destinations
    for feature in ("web_export", "web_preview"):
        chat_destinations.unregister(feature)
    web_preview.unregister()
    bpy.types.TOPBAR_MT_file_export.remove(draw_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
