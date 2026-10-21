# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Default chat-first workspace with an always-available manual-mode escape.

Director hides manual chrome, never input: every viewport navigation,
selection, transform and menu shortcut reaches Blender natively. It adds no
input handler of its own (an earlier passive-preview modal swallowed them).
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
    source, pointer = window.workspace, window.as_pointer()
    job = build(window) if mode == "DIRECTOR" else restore(window)
    last_error = None
    def advance():
        global _timer, last_error
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            _timer = None
            return None
        if layout_blocking(target):
            return .1
        try:
            return next(job)
        except StopIteration:
            _timer = None
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
        # Director hides the Help menu, so the introduction is offered here too.
        self.layout.separator()
        self.layout.operator("angelo.intro", icon='INFO')


def draw_topbar(self, context):
    active = is_director(context.window)
    if context.region.alignment == 'RIGHT':
        # App-wide presentation belongs beside scene scope, not in a chat tab.
        if not active:   # Show or hide the chat, beside the mode menu (chat_toggle).
            from . import chat_toggle
            chat_toggle.draw(self.layout, context)
        mode = self.layout.row(align=True)
        mode.ui_units_x = 6
        mode.menu("ANGELO_MT_workspace_mode", text=iface_("Director") if active else iface_("Manual", "Angelo"), translate=False, icon='OUTLINER_OB_CAMERA' if active else 'TOOL_SETTINGS')
        self.layout.separator()
        if active:
            # Scope selection is still available, without manual New/Delete or
            # view-layer properties. The existing document guard handles changes.
            self.layout.template_search(context.window, "scene", context.blend_data, "scenes")
            return
    if active:
        self.layout.label(text="Angelo · Director Mode")
        draw_director_status(self, context)
        return
    _header_draw(self, context)


def draw_director_status(header, context):
    """Director hides the status bar, so its reports and running work show beside the mode's title.

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
