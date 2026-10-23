# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Distinct editor choices, with a stock-binary picker compatibility layer.

The compiled fork exposes CODING_AGENT natively through Area.ui_type. Until a
fork build exists, only template_header's editor picker is replaced; every
other header control and Python's interpreter/keymap stay native. This does not
rename the existing CONSOLE entry or redirect its execution to the agent.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator
from .editor_registry import CODING_AGENT, GENERATIVE_AI, EDITOR_GROUPS, EDITORS, editor_identity, cached_editor_identity
from .drafts import console_byte_offset, read_chat_draft as _read_chat_draft, write_chat_draft as _write_chat_draft
from .display_text import Text
from .ui_text import iface_, n_

_headers = {}
_buffers = {}
_language_draw = None
last_error = None
_drawn_headers = set()


def native_picker():
    return "is_coding_agent" in bpy.types.SpaceConsole.bl_rna.properties


def _snapshot(space):
    return {"history": [(l.body, l.current_character) for l in space.history],
            "scrollback": [(l.body, l.type) for l in space.scrollback],
            "prompt": space.prompt, "font_size": space.font_size}


def require_chat_draft_access():
    if native_picker() and "coding_agent_input" not in bpy.types.SpaceConsole.bl_rna.properties:
        raise RuntimeError(Text(n_("Rebuild Angelo before switching chats: this native editor lacks safe cached-draft access")))


def read_chat_draft(space):
    return _read_chat_draft(space, _buffers)


def write_chat_draft(space, draft):
    _write_chat_draft(space, draft, _buffers)


def mark_angelo_owned(window, area):
    """Mark ``area`` as one Angelo draws whole (``Area.angelo_owned``, 1.0.5 native: no region-toggle arrows, saved in
    the .blend). Set before hiding its regions: setting it re-inits the area. A build without it is left as it is."""
    if hasattr(area, "angelo_owned") and not area.angelo_owned:
        with bpy.context.temp_override(window=window, screen=window.screen, area=area):
            area.angelo_owned = True


def set_editor(window, area, ui_type=None, area_type=None):
    """Change ``area``'s editor with its window in the context. Blender sets up the new editor's regions through
    the context's window: from a timer, where there is none, the editor was left without regions and its window drew
    nothing at all (1.0.5: Media Studio opened from the welcome card was a blank window)."""
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        if ui_type is not None:
            area.ui_type = ui_type
        else:
            area.type = area_type


def restore_previous_editor(window, area):
    previous = next((s for s in area.spaces if s.type != "CONSOLE"), None)
    identity = cached_editor_identity(previous) if previous else "VIEW_3D"
    # Area.ui_type distinguishes UV/Timeline but not every image/dope-sheet
    # editing mode. Retain those finer modes as well as the editor identity.
    mode = previous.mode if previous and previous.type in {"IMAGE_EDITOR", "DOPESHEET_EDITOR"} else None
    with bpy.context.temp_override(window=window, area=area):
        area.ui_type = identity
        restored = area.spaces.active
        if restored.type == "IMAGE_EDITOR" and mode is not None and restored.mode != mode:
            restored.mode = mode
        elif restored.type == "DOPESHEET_EDITOR" and mode not in {None, "TIMELINE"} and restored.mode != mode:
            restored.mode = mode


def select_console(window, area, coding=False, generative=False):
    """Keep Python input/output separate from the agent composer in a preview."""
    from . import console, chat_view, ui
    target = "angelo_genai" if generative else console.language_id if coding else "python"
    previous = next((s for s in area.spaces if s.type == "CONSOLE"), None)
    changed = previous is None or (previous.language or "python") != target
    native = native_picker()
    if previous:
        language = previous.language or "python"
        if language != target:
            if not native:
                _buffers.setdefault(previous.as_pointer(), {})[language] = _snapshot(previous)
            if language == console.language_id:
                ui.remember_draft(ui.get_controller())
    if native:
        set_editor(window, area, ui_type=GENERATIVE_AI if generative else CODING_AGENT if coding else "CONSOLE")
    else:
        set_editor(window, area, area_type="CONSOLE")
    space = area.spaces.active
    if not native and (changed or space.language != target):
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.console.clear(scrollback=True, history=True)
                space.language = target
                saved = _buffers.get(space.as_pointer(), {}).get(target)
                if saved:
                    for body, kind in saved["scrollback"]:
                        bpy.ops.console.scrollback_append(text=body, type=kind)
                    for body, cursor in saved["history"]:  # Saved as ConsoleLine.current_character: characters.
                        bpy.ops.console.history_append(text=body, current_character=console_byte_offset(body, cursor),
                                                       remove_duplicates=False)
                    space.prompt, space.font_size = saved["prompt"], saved["font_size"]
                elif target == "python":
                    bpy.ops.console.banner()
                if not space.history:
                    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
    if not native:
        space.language = target
    view = chat_view._views.get(area.as_pointer())
    if view:
        view.pop("popover", None)
        view["focused"] = False
    from . import studio
    if area.as_pointer() in studio._views:
        studio._views[area.as_pointer()]["focus"] = None
    console._versions.pop(area.as_pointer(), None)
    console._states.pop(area.as_pointer(), None)
    area.tag_redraw()


class ANGELO_OT_editor_type(Operator):
    bl_idname = "angelo.editor_type"
    bl_label = "Editor Type"
    bl_description = "Choose the editor for this area"
    ui_type: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import ui
        if not context.area or self.ui_type not in EDITORS:
            return {'CANCELLED'}
        try:
            if self.ui_type == CODING_AGENT:
                if ui._main_window_pointer is None:
                    ui._main_window_pointer = context.window.as_pointer()
                ui.setup_chat_area(context.window, context.area)
            elif self.ui_type == "CONSOLE":
                select_console(context.window, context.area, coding=False)
            elif self.ui_type == GENERATIVE_AI:
                from . import studio
                studio.setup_area(context.window, context.area)
            else:
                context.area.ui_type = self.ui_type
        except (TypeError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def _icon_kwargs(identity, icon):
    """Media Studio shows its own spark frame (one icon per concept, studio_entry_points.rst section 2); every other
    editor its Blender icon."""
    if identity == GENERATIVE_AI:
        from .studio_toggle import icon_id
        return {"icon_value": icon_id(False)}
    return {"icon": icon}


class ANGELO_MT_editor_type(Menu):
    bl_label = "Editor Type"

    def draw(self, context):
        row = self.layout.row()
        for title, entries in EDITOR_GROUPS:
            column = row.column()
            column.label(text=title)
            column.separator()
            for identity, name, icon in entries:
                # Blender's own editor names (and Angelo's two, in its catalogue), in the default context.
                column.operator("angelo.editor_type", text=iface_(name), translate=False,
                                **_icon_kwargs(identity, icon)).ui_type = identity


class _LayoutProxy:
    def __init__(self, layout, context):
        object.__setattr__(self, "_layout", layout)
        object.__setattr__(self, "_context", context)

    def template_header(self, *args, **kwargs):
        identity = editor_identity(self._context.area)
        _, icon = EDITORS.get(identity, (identity, "NONE"))
        _drawn_headers.add(self._context.area.type)
        self._layout.menu("ANGELO_MT_editor_type", text="", **_icon_kwargs(identity, icon))

    def __getattr__(self, name):
        value = getattr(self._layout, name)
        if not callable(value):
            return value
        def call(*args, **kwargs):
            args = tuple(a._layout if isinstance(a, _LayoutProxy) else a for a in args)
            kwargs = {k: v._layout if isinstance(v, _LayoutProxy) else v for k, v in kwargs.items()}
            result = value(*args, **kwargs)
            return _LayoutProxy(result, self._context) if isinstance(result, bpy.types.UILayout) else result
        return call

    def __setattr__(self, name, value):
        setattr(self._layout, name, value)


class _HeaderProxy:
    def __init__(self, header, context):
        self._header = header
        self.layout = _LayoutProxy(header.layout, context)

    def __getattr__(self, name):
        value = getattr(self._header, name)
        if getattr(value, "__self__", None) is self._header and hasattr(value, "__func__"):
            return lambda *args, **kwargs: value.__func__(self, *args, **kwargs)
        return value


def _wrap_header(draw):
    def wrapped(self, context):
        global last_error
        try:
            draw(_HeaderProxy(self, context), context)
        except Exception as error:
            last_error = str(error)
            # Fail back to Blender's header rather than breaking the editor.
            draw(self, context)
    return wrapped


def draw_languages(self, context):
    import sys
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("_console_") and hasattr(module, "execute"):
            language = module_name.split("_", 2)[-1]
            if language not in {"angelo", "angelo_genai"}:
                self.layout.operator("console.language", text=language.title(), translate=False).language = language


def register():
    global _language_draw
    bpy.utils.register_class(ANGELO_OT_editor_type)
    bpy.utils.register_class(ANGELO_MT_editor_type)
    # A menu's RNA draw callback is cached; re-register the original type with
    # the filtered language menu. Unregister restores its exact original draw.
    language_type = bpy.types.CONSOLE_MT_language
    _language_draw = language_type.draw
    bpy.utils.unregister_class(language_type)
    language_type.draw = draw_languages
    bpy.utils.register_class(language_type)
    if native_picker():
        return
    for header in list(bpy.types.Header.__subclasses__()):
        if not getattr(header, "is_registered", False) or not hasattr(header, "draw"):
            continue
        if getattr(header, "bl_region_type", 'HEADER') != 'HEADER' or getattr(header, "bl_space_type", "") in {'TOPBAR', 'STATUSBAR'}:
            continue
        draw = header.draw
        bpy.utils.unregister_class(header)
        header.draw = _wrap_header(draw)
        bpy.utils.register_class(header)
        _headers[header] = draw


def unregister():
    global _language_draw, last_error
    for header, draw in _headers.items():
        bpy.utils.unregister_class(header)
        header.draw = draw
        bpy.utils.register_class(header)
    _headers.clear()
    if _language_draw:
        language_type = bpy.types.CONSOLE_MT_language
        bpy.utils.unregister_class(language_type)
        language_type.draw = _language_draw
        bpy.utils.register_class(language_type)
        _language_draw = None
    bpy.utils.unregister_class(ANGELO_MT_editor_type)
    bpy.utils.unregister_class(ANGELO_OT_editor_type)
    _buffers.clear()
    _drawn_headers.clear()
    last_error = None
