# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Default chat-first workspace with an always-available manual-mode escape.

Director hides manual chrome, never input: every viewport navigation,
selection, transform and menu shortcut reaches Blender natively. It adds no
input handler of its own (an earlier passive-preview modal swallowed them).

Director keeps Blender's own top-bar menus (Angelo, File, Edit, Render, Window,
Help) and hides only the workspace tabs. Before 1.0.4 it replaced them with a
title, so Save, Open, Undo, Export and Preferences had no visible control and
their shortcuts were named nowhere (user report, 2026-10-21: "I have to switch
back to manual mode to e.g. save the file"); entries were re-added one at a time
to the mode menu instead. The menus themselves are the whole fix: nothing in
them can go missing, and each item shows its shortcut.
"""

from pathlib import Path

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, Menu
from bpy.app.handlers import persistent

from . import director_layout
from .director_layout import is_director, configure, build, restore
from .studio_state import read_json, atomic_json
from .input_policy import layout_blocking
from .display_text import Text, shown
from .ui_text import iface_, n_, notice

_timer = None
_generation = 0
_header_draw = None
_keymaps = []
last_error = None


def mode_path():
    from .ui import state_directory
    return Path(state_directory()) / "workspace-mode.json"


def preferred_mode():
    value = read_json(mode_path(), {})
    return "MANUAL" if isinstance(value, dict) and value.get("mode") == "MANUAL" else "DIRECTOR"


def request_mode(window, mode, *, persist=True):
    """Defers destructive area changes outside draw/input/operator handlers."""
    global _timer, last_error
    if mode not in {"DIRECTOR", "MANUAL"}:
        raise ValueError("Choose DIRECTOR or MANUAL")
    if _timer is not None:
        raise RuntimeError(Text(n_("The workspace mode is already changing")))
    from . import ui
    if ui._layout_timer is not None:
        raise RuntimeError(Text(n_("Wait for the chat dock to finish moving")))
    if window.screen.is_temporary:
        raise RuntimeError(Text(n_("Return to the main workspace before changing mode")))
    from . import chat_launcher
    source, pointer = window.workspace, window.as_pointer()
    job = build(window) if mode == "DIRECTOR" else restore(window)
    last_error = None
    def advance():
        global _timer, last_error
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            _timer = None
            chat_launcher.settled(pointer)   # Its window closed: the other windows' Angelo shows now.
            return None
        if layout_blocking(target):
            return .1
        try:
            return next(job)
        except StopIteration:
            _timer = None
            chat_launcher.settled(pointer)   # Corner Angelo shows or goes now.
            if persist:
                try:
                    atomic_json(mode_path(), {"mode": mode})
                except OSError as error:
                    ui.get_controller().emit("message", {"role": "error", **notice("Mode changed, but the preference could not be saved: {error}", error=str(error))})
            return None
        except Exception as error:
            last_error = str(error)
            # A failed disposable copy never strands the user without controls.
            if source in bpy.data.workspaces[:]:
                target.workspace = source
            _timer = None
            chat_launcher.settled(pointer)   # Corner Angelo shows or goes now.
            ui.get_controller().emit("message", {"role": "error", **notice("Director Mode: {error}", error=last_error)})
            return None
    _timer = advance
    bpy.app.timers.register(advance, first_interval=.05)


class ANGELO_OT_workspace_mode(Operator):
    bl_idname = "angelo.workspace_mode"
    bl_label = "Workspace Mode"
    bl_description = "Director hides manual controls; Show controls restores your original workspace"
    mode: EnumProperty(items=[("DIRECTOR", "Director Mode", "Direct the scene and Media Studio through chat"),
                              ("MANUAL", "Show Controls", "Restore manual editors and properties")], default="DIRECTOR")

    def execute(self, context):
        try:
            request_mode(context.window, self.mode)
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_director_toggle(Operator):
    bl_idname = "angelo.director_toggle"
    bl_label = "Toggle Director Mode"

    def execute(self, context):
        return bpy.ops.angelo.workspace_mode(mode="MANUAL" if is_director(context.window) else "DIRECTOR")


class ANGELO_OT_director_toggle_viewport(Operator):
    """1.0's Ctrl+Option+D over the 3D view (``shortcuts.DIRECTOR_VIEWPORT``): never shown, so never in search."""
    bl_idname = "angelo.director_toggle_viewport"
    bl_label = "Toggle Director Mode"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return bpy.ops.angelo.director_toggle()


class ANGELO_MT_workspace_mode(Menu):
    bl_label = "Workspace Mode"

    def draw(self, context):
        active = is_director(context.window)
        # The mode a click switches to is the toggle, so the menu shows its shortcut (shortcuts.DIRECTOR) beside it.
        if active:
            self.layout.operator("angelo.workspace_mode", text="Director Mode", icon='RADIOBUT_ON').mode = "DIRECTOR"
            self.layout.operator("angelo.director_toggle", text="Manual Mode", icon='RADIOBUT_OFF')
        else:
            self.layout.operator("angelo.director_toggle", text="Director Mode", icon='RADIOBUT_OFF')
            self.layout.operator("angelo.workspace_mode", text="Manual Mode", icon='RADIOBUT_ON').mode = "MANUAL"
        # The introduction is also offered beside the mode it explains (Help has it too).
        self.layout.separator()
        self.layout.operator("angelo.intro", icon='INFO')


def window_menu(self, context):
    """Window > Angelo Chat and the other mode, in both modes (Director keeps Blender's menus since 1.0.4). The mode
    item is the toggle, so its shortcut shows beside it."""
    from . import studio_toggle
    self.layout.separator()
    self.layout.operator("angelo.open_chat", text="Angelo Chat")
    studio_toggle.menu_entries(self.layout, "TOPBAR_MT_window")   # Media Studio and Generation Flows beside it.
    self.layout.operator("angelo.director_toggle", text="Manual Mode" if is_director(context.window) else "Director Mode")


def draw_topbar(self, context):
    active = is_director(context.window)
    if context.region.alignment == 'RIGHT':
        # App-wide presentation belongs beside scene scope, not in a chat tab. Show or hide the chat, beside the mode
        # menu, the same button in both modes (chat_toggle).
        from . import chat_toggle, flow_open, studio_toggle, top_bar_fit
        # Media Studio and Generation Flows beside Chat, permanently, in both modes (1.0.5): Director shows no editor-type
        # menu to find them in, and Manual's should not be the only way. When the bar lacks room the less important
        # items give way first and these two only lose their labels (top_bar_fit.compact).
        names = (iface_("Director"), iface_("Manual", "Angelo"))
        level = top_bar_fit.compact(context, active, {
            "chat": chat_toggle.button_text(), "chat_short": iface_(chat_toggle.LABEL), "studio": studio_toggle.label(),
            "flows": iface_(flow_open.TOP_BAR_LABEL), "mode": max(names, key=len)})
        chat_toggle.draw(self.layout, context, level >= top_bar_fit.CHAT_SHORT, level >= top_bar_fit.CHAT_ICON)
        studio_toggle.draw(self.layout, context, level >= top_bar_fit.STUDIO_ICONS)
        flow_open.draw_top_bar(self.layout, context, level >= top_bar_fit.STUDIO_ICONS)
        # Wide enough for either mode's name in this language, so it never changes width.
        mode = top_bar_fit.fitted_row(self.layout, context, "mode", names[0 if active else 1], also=names)
        # The slot keeps the wider name's width, so the bar never moves between modes; the button hugs the shown name
        # at the slot's left, its icon right beside it (a button the slot's width centred the shorter name and left
        # the camera or wrench far from it: 1.0.5 review SD1).
        mode.alignment = 'LEFT'
        # As wide as Blender's own estimate for a header menu (text + 1.1 units, interface_layout.cc) plus a quarter
        # unit: left to the estimate it drew "Direc…" (1.0.5 RC, chat_start_states); a button sized like Chat, Studio
        # and Flows (text + 1.75 units) centred the label 9 px from its icon (interim dbe022dddb9, limit 6 x scale).
        shown = mode.row()
        shown.ui_units_x = top_bar_fit.menu_units(context, names[0 if active else 1])
        shown.menu("ANGELO_MT_workspace_mode", text=names[0 if active else 1], translate=False,
                   icon='OUTLINER_OB_CAMERA' if active else 'TOOL_SETTINGS')
        self.layout.separator()
        if active:
            # Scope selection is still available, without manual New/Delete or
            # view-layer properties. The existing document guard handles changes.
            self.layout.template_search(context.window, "scene", context.blend_data, "scenes")
            return
    if active:
        # Blender's own menus, as in Manual mode; only the workspace tabs stay hidden (the mode menu names the mode).
        from bl_ui.space_topbar import TOPBAR_MT_editor_menus
        TOPBAR_MT_editor_menus.draw_collapsible(context, self.layout)
        draw_director_status(self, context)
        return
    if context.region.alignment == 'RIGHT' and level >= top_bar_fit.NO_VIEW_LAYER:
        draw_scene_only(self, context)   # The view layer selector gives way (Properties > View Layer keeps it).
        return
    _header_draw(self, context)


def draw_scene_only(header, context):
    """Blender's right-hand top bar (``TOPBAR_HT_upper_bar.draw_right``) without its view layer selector."""
    layout, window = header.layout, context.window
    if not context.screen.show_statusbar:
        layout.template_reports_banner()
        layout.template_running_jobs()
    layout.template_ID(window, "scene", new="scene.new", unlink="scene.delete")


def draw_director_status(header, context):
    """Director hides the status bar, so its reports and running work show after the menus.

    The report banner and Blender's job progress are the status bar's own templates; the top bar
    redraws on the same notifiers (report timer and job updates). The agent's background job item
    follows with its Cancel or Open action as an icon button, so a translated word is never clipped
    in the narrow bar (the action's name is its tooltip).
    """
    import time
    from . import job_ui
    layout = header.layout
    layout.separator(factor=2.0)
    layout.template_reports_banner()
    layout.template_running_jobs()
    item = job_ui.status_item(job_ui.manager(create=False), time.monotonic())
    if item is None:
        return
    text, icon, action, identity = item
    row = layout.row(align=True)
    row.separator()
    row.label(text=text, icon=icon, translate=False)
    row.emboss = 'NONE'
    if action == "cancel":
        row.operator(job_ui.ANGELO_OT_job_cancel.bl_idname, text="", icon='CANCEL').job = identity
    elif action == "open":
        row.operator(job_ui.ANGELO_OT_job_open.bl_idname, text="", icon='FILE_FOLDER').job = identity


@persistent
def load_pre(_):
    global _timer, _generation
    _generation += 1
    _timer = None  # Blender discards the non-persistent layout timer on load.


def register():
    global _header_draw, _generation
    _generation += 1
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    header = bpy.types.TOPBAR_HT_upper_bar
    _header_draw = header.draw
    bpy.utils.unregister_class(header)
    header.draw = draw_topbar
    bpy.utils.register_class(header)
    bpy.app.handlers.load_pre.append(load_pre)
    bpy.types.TOPBAR_MT_window.append(window_menu)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        from . import shortcuts
        _keymaps.append(shortcuts.add(keyconfig, shortcuts.DIRECTOR))
        _keymaps.append(shortcuts.add(keyconfig, shortcuts.DIRECTOR_VIEWPORT))


def unregister():
    global _generation, _timer, _header_draw
    _generation += 1
    if _timer and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer = None
    if load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(load_pre)
    bpy.types.TOPBAR_MT_window.remove(window_menu)
    # Disabling the add-on returns to the manual workspace.
    for window in bpy.context.window_manager.windows:
        if is_director(window):
            target = bpy.data.workspaces.get(director_layout.manual_workspace_name(window.workspace))
            if target and not director_layout.is_director_workspace(target):
                window.workspace = target
    if _header_draw:
        header = bpy.types.TOPBAR_HT_upper_bar
        bpy.utils.unregister_class(header)
        header.draw = _header_draw
        bpy.utils.register_class(header)
        _header_draw = None
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


CLASSES = (ANGELO_OT_workspace_mode, ANGELO_OT_director_toggle, ANGELO_OT_director_toggle_viewport,
           ANGELO_MT_workspace_mode)
