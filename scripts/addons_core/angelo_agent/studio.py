# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio (the console's angelo_genai editor), rendered with Blender's native GPU.

The library belongs to the app, not to a chat or .blend file. Create/Folders/Grid
occupy separate columns; collapsing Create never overlays the Blender viewport.
Only explicit Generate and Enhance confirmations can incur provider usage.
"""

import queue
from pathlib import Path

import bpy
import gpu

from . import card_kit as kit, chat_view as canvas, studio_views, ui_tokens as tokens
from .studio_state import StudioLibrary, model_options, studio_layout, setup_text, FOLDER_ROWS, OPTION_LABELS
from .studio_generation import StudioJobs
from .provider_registry import PROVIDER_NAMES, SPECS
from .references import ReferenceStore, MIME
from .studio_previews import release_preview
from .ui_text import iface_, n_

language_id = "angelo_genai"
# Every colour is a shared design token (doc/angelo/studio_entry_points.rst, "Visual language"; the maintainer, 10-20:
# Media Studio wears the same palette as every route into it). No literal colour in studio*.py (a host test pins it).
BG = kit.CARD
PANEL = kit.PANEL
FIELD = kit.CARD
LINE = kit.LINE
HOVER = kit.PANEL_HOVER
ACCENT = kit.TEAL                  # A fill: the primary button, the active tab, a progress bar. INK text on it.
ACCENT_HOVER = kit.TEAL_HOVER
ACCENT_PRESSED = kit.TEAL_PRESSED
ACCENT_TEXT = kit.INK
FOCUS = kit.FOCUS                  # A focused field's outline.
SELECTED = kit.TEAL_SOFT           # A chosen chip (1:1, 2K), the active folder.
TEXT = kit.TEXT
MUTED = kit.MUTED
GREEN = tokens.SUCCESS
SCRIM = kit.SCRIM
POPUP_ROW = 43  # A popover entry's pitch, in pixels.
ERROR = tokens.DANGER

_views, _modals, _textures = {}, set(), {}
_handler, _image_shader = None, None
_library, _jobs, _references = None, None, None
_enabled = False
_input_generation = 0
_enhancement_events = queue.Queue()
_enhancements = {}
_enhancement_connections = {}
_enum_cache = {}
last_error = None
draw_count = 0


def is_studio(area):
    return bool(area and area.type == 'CONSOLE' and area.spaces.active.language == language_id)


def library():
    global _library, _jobs, _references
    if _library is None:
        _library = StudioLibrary()
        _jobs = StudioJobs(_library)
        import uuid
        _references = ReferenceStore(_library.directory, str(uuid.uuid4()), image_limit=20 * 1024 * 1024)
    return _library


def view(area):
    return studio_views.view(_views, area, is_studio)   # A Studio window's state follows the window.


def options(state):
    from . import ui
    return model_options(state["kind"], ui.generation_connections(), ui.generation_health())


def selected(state):
    entries = options(state)
    identity = state["models"].get(state["kind"])
    if identity:
        # An unavailable/removed selected account never silently falls back.
        found = next((m for m in entries if (m["connection"], m["id"]) == tuple(identity)), None)
        if found:
            return found
        return {"id": identity[1], "name": n_("Reconnect selected model"), "connection": identity[0], "ready": False,
                "mode": "", "account": n_("Open AI Providers"), "verified": False}
    return next((m for m in entries if m["ready"]), entries[0] if entries else None)


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if is_studio(area):
                area.tag_redraw()


def connection_in_use(identity):
    return bool((_jobs and any(_library.assets.get(a, {}).get("connection") == identity for a in _jobs.active))
                or identity in _enhancement_connections.values())


def setup_area(window, area):
    """A person turns ``area`` into Media Studio (the editor-type menu): refused while a Blender tool is active, as
    changing the editor under it would."""
    from .input_policy import interaction_active
    if interaction_active(window):
        raise RuntimeError(n_("Finish or cancel the active Blender tool before opening Media Studio"))
    from .editor_types import select_console
    select_console(window, area, generative=True)
    prepare_area(window, area)


def prepare_area(window, area):
    """Seed and bind an area that already is Media Studio (no editor change): safe whatever modal runs."""
    area.spaces.active.prompt = ""
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region:
        # Console calls banner on every draw while scrollback is empty. Seed
        # both native buffers once so bootstrap cannot steal field focus.
        with bpy.context.temp_override(window=window, area=area, region=region):
            if not area.spaces.active.history:
                bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
            if not area.spaces.active.scrollback:
                bpy.ops.console.scrollback_append(text=iface_("Media Studio · images and 3D models"), type='INFO')
    library()
    view(area)
    ensure_input(window, area)
    area.tag_redraw()


def banner(context):
    """Blender's console banner, on a Media Studio area's first draw: its tab or window shown at any time, with the
    startup splash, setup card or welcome open too. The area already is Media Studio, so it is prepared without the
    refusal a person's editor switch gets (that raised from a draw at startup: 1.0.5, "Finish or cancel the active
    Blender tool"), and nothing here ever raises out of the operator."""
    try:
        prepare_area(context.window, context.area)
    except Exception as error:   # noqa: BLE001 - the next draw calls the banner again; one line, no traceback.
        print("Angelo: Media Studio's area will be prepared on its next draw:", error)
        return {'CANCELLED'}
    return {'FINISHED'}


def execute(context, is_interactive=False):
    return {'CANCELLED'}  # Enter edits the prompt; generation is explicit.


def autocomplete(context):
    return {'CANCELLED'}


def copy_as_script(context):
    return {'CANCELLED'}


def draw():
    global last_error, draw_count
    area, region = bpy.context.area, bpy.context.region
    if not _enabled or not is_studio(area) or region.type != 'WINDOW':
        return
    try:
        state = view(area)
        width, height = region.width, region.height
        if width < 300 or height < 150:
            canvas.shape(0, 0, width, height, BG)
            canvas.text(iface_("Media Studio · widen this editor"), 12, max(10, height - 24), 11, TEXT)
            return
        if state["collapsed"] is None:
            state["collapsed"] = width < 740
        state["hits"], state["fields"] = [], {}
        state["geometry"] = studio_layout(width, height, state["collapsed"])
        gpu.state.blend_set('ALPHA')
        canvas.shape(0, 0, width, height, BG)
        button(state, (14, height - 45, 95, 32), iface_("Image"), "tab", "IMAGE", active=state["kind"] == "IMAGE", primary=state["kind"] == "IMAGE")
        button(state, (117, height - 45, 73, 32), iface_("3D"), "tab", "3D", active=state["kind"] == "3D", primary=state["kind"] == "3D")
        button(state, (204, height - 45, 112, 32), iface_("Create") if state["collapsed"] else iface_("Hide Create"), "collapse", icon="plus" if state["collapsed"] else None)
        top_row_end(state, width, height)
        for name, function in (("create", create_panel), ("folders", folder_panel), ("assets", asset_grid)):
            rect = state["geometry"][name]
            if rect[2] > 0 and rect[3] > 0:
                function(state, rect)
        if state.get("notice"):
            notice = canvas.elide(iface_(state["notice"]), max(100, width - 58), 11)
            canvas.shape(14, 15, width - 28, 35, HOVER, 8)
            canvas.text(notice, 26, 27, 11, TEXT)
            canvas.icon("close", width - 31, 32)
            hit(state, (width - 47, 15, 33, 35), "clear_notice")
        popup(state, width, height)
        expanded_prompt(state, width, height)
        gpu.state.blend_set('NONE')
        draw_count += 1
    except Exception as error:
        last_error = str(error)
        print("Angelo Media Studio draw:", error)
        gpu.state.scissor_test_set(False)
        gpu.state.blend_set('NONE')


# Media Studio's Asset Browser library row (studio_draw.library_rows): action -> operator.
LIBRARY_ACTIONS = {"library_off": "studio_library_off", "library_on": "studio_library_on",
                   "library_free": "studio_library_free"}

# Studio's top row: actions there are never clipped to the Create column (studio_draw.create_panel).
TOP_ROW_ACTIONS = frozenset({"tab", "collapse", "to_flow"})


def top_row_end(state, width, height):
    """The right end of Studio's top row: "Turn into a flow" (Part A's ``studio_to_flow.draw_pill(state, width,
    height, canvas=canvas)``; its action "to_flow" is handled in ``click``), else the muted "Local media library" label.
    The one hook for that pill."""
    try:
        from . import studio_to_flow
    except ImportError:
        studio_to_flow = None
    if studio_to_flow is not None:
        studio_to_flow.draw_pill(state, width, height, canvas=canvas)   # Its pill draws with the canvas's primitives.
    elif width > 600:
        canvas.text(iface_("Local media library"), width - 144, height - 34, 11, MUTED)


def open_popup(state, action, anchor):
    entries, title = [], ""
    if action == "model":
        title = n_("Select model")
        current = selected(state)
        for model in options(state):
            entries.append({"label": model["name"], "description": iface_("{provider} · {account}", provider=PROVIDER_NAMES[SPECS[model["mode"]]["provider"]], account=setup_text(model, "account")),
                "value": (model["connection"], model["id"]), "selected": bool(current and (current["connection"], current["id"]) == (model["connection"], model["id"]))})
    elif action == "folder_picker":
        title = n_("Folders")
        entries = [{"label": name, "value": identity, "selected": state["folder"] == identity, "ui": identity in {"ALL", "UNFILED"}}
            for identity, name in list(FOLDER_ROWS) + [(r["id"], r["name"]) for r in library().folders.values()]]
        status, actions = library_rows()   # The narrow layout has no Folders column: its library row is here.
        entries += [{"label": iface_("Asset Browser: {action} ({status})", action=text, status=status), "value": action}
                    for text, action in actions]
    else:
        title = n_("Quality") if action == "quality" else n_("Background")
        choices = ("auto", "low", "medium", "high", "xhigh", "max") if action == "quality" else ("auto", "opaque", "transparent")
        entries = [{"label": OPTION_LABELS[c], "value": c, "selected": state[action] == c, "ui": True} for c in choices]
    state["popover"] = {"action": action, "title": title, "entries": entries, "anchor": (anchor[0], anchor[1] + anchor[3]), "scroll": 0}


def click(context, state, action, value, rect):
    if action == "tab":
        state.update(kind=value, create_scroll=0, grid_scroll=0, focus=None, selection=None)
    elif action == "collapse":
        state["collapsed"] = not state["collapsed"]
        state["focus"] = None
    elif action == "focus":
        state.update(focus=value, caret=len(state[value]), selection=None)
    elif action in {"model", "quality", "background", "folder_picker"}:
        state["focus"] = None
        open_popup(state, action, rect)
    elif action in {"aspect", "resolution"}:
        state[action] = value
    elif action == "texture":
        state["texture"] = not state["texture"]
    elif action == "folder":
        state.update(folder=value, grid_scroll=0)
    elif action in LIBRARY_ACTIONS:
        state["focus"] = None
        getattr(bpy.ops.angelo, LIBRARY_ACTIONS[action])('INVOKE_DEFAULT')
    elif action == "settings":
        bpy.ops.angelo.settings('INVOKE_DEFAULT')
    elif action == "refresh":
        library().reload()
    elif action in {"reference", "import"}:
        bpy.ops.angelo.studio_files('INVOKE_DEFAULT', purpose="REFERENCE" if action == "reference" else "IMPORT", area_pointer=str(context.area.as_pointer()))
    elif action == "url":
        bpy.ops.angelo.studio_url('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
    elif action == "remove_reference":
        state["references"] = [r for r in state["references"] if r["id"] != value]
    elif action == "folder_new":
        bpy.ops.angelo.studio_folder('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
    elif action == "generate":
        bpy.ops.angelo.studio_generate('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
    elif action == "enhance":
        bpy.ops.angelo.studio_enhance('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
    elif action == "expand":
        bpy.ops.angelo.studio_prompt('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()), prompt=state["prompt"])
    elif action == "review_enhance":
        bpy.ops.angelo.studio_prompt('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()), prompt=state["enhanced"], enhanced=True)
    elif action == "discard_enhance":
        state["enhanced"] = None
    elif action == "undo_enhance":
        state["prompt"] = state.pop("original_prompt")
    elif action == "prompt_apply":
        if state["prompt_dialog"].get("enhanced"):
            state["original_prompt"] = state["prompt"]
            state["enhanced"] = None
        state["prompt"] = state["expanded_prompt"]
        state.pop("prompt_dialog", None)
        state["focus"] = None
    elif action == "prompt_cancel":
        state.pop("prompt_dialog", None)
        state["focus"] = None
    elif action == "asset":
        bpy.ops.angelo.studio_asset('INVOKE_DEFAULT', asset_id=value, area_pointer=str(context.area.as_pointer()))
    elif action == "to_flow":
        from . import studio_to_flow
        studio_to_flow.turn_into_flow(context, state)
    elif action == "clear_notice":
        state["notice"] = ""
    redraw()


def attach(state, paths):
    if any(Path(p).suffix.lower() not in MIME for p in paths):
        raise ValueError(n_("Media Studio references must be PNG, JPEG or WebP images"))
    state["references"].extend(_references.import_images(paths, state["references"]))
    state["notice"] = n_("References added. Images are sent only when you confirm Generate.")


def defer_drop(window, area):
    line = area.spaces.active.history[-1] if area.spaces.active.history else None
    before = line.body if line else ""
    pointer, wp = area.as_pointer(), window.as_pointer()

    def finish():
        current_window = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == wp), None)
        target = next((a for a in current_window.screen.areas if a.as_pointer() == pointer and is_studio(a)), None) if current_window else None
        if not target or not target.spaces.active.history:
            return None
        history = target.spaces.active.history[-1]
        inserted = history.body[len(before):]
        history.body = before
        if inserted.startswith('"') and inserted.endswith('"'):
            try:
                attach(view(target), [inserted[1:-1]])
            except (OSError, ValueError) as error:
                view(target)["notice"] = str(error)
        redraw()
        return None
    bpy.app.timers.register(finish, first_interval=.03)


def cancel_requests():
    """Before a file loads (``studio_views.on_load``): AI Enhance requests in flight stop, and a reply already sent is
    dropped by the pump, so nothing from the file going away lands in the next one."""
    for cancel in _enhancements.values():
        cancel.set()
    _enhancements.clear()
    _enhancement_connections.clear()


def pump():
    if not _enabled:
        return None
    changed = _jobs.pump() if _jobs else False
    while not _enhancement_events.empty():
        pointer, result = _enhancement_events.get_nowait()
        if _enhancements.pop(pointer, None) is None:
            continue   # Cancelled (a file load or unregister): it answers to nothing shown now.
        _enhancement_connections.pop(pointer, None)
        state = _views.get(int(pointer))
        if state:
            state.update(result, enhancing=False)
            changed = True
    # Supports areas restored from a saved .blend and new/floating windows.
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if is_studio(area):
                view(area)
                ensure_input(window, area)
    studio_views.track(_views, {int(p) for p in _enhancements if str(p).isdigit()})   # Closed windows', freed areas' states go.
    if changed:
        redraw()
    return .1


# The split modules (their 500-line limit): imported last, as they import what is defined above. Every name
# stays reachable as ``studio.<name>``.
from .studio_draw import (  # noqa: E402,F401
    asset_grid, button, create_panel, expanded_prompt, field, folder_panel, hit, label, library_rows, paragraph,
    popup, thumbnail)
from .studio_input import (  # noqa: E402,F401
    ANGELO_OT_studio_input, ensure_input, point_to_caret, state_for)
from .studio_ops import (  # noqa: E402,F401
    ANGELO_FH_studio, ANGELO_OT_studio_enhance, ANGELO_OT_studio_files, ANGELO_OT_studio_folder,
    ANGELO_OT_studio_generate, ANGELO_OT_studio_prompt, ANGELO_OT_studio_url, enhancement_binding)
from .studio_asset_ops import (  # noqa: E402,F401
    ANGELO_OT_enhancer_refresh, ANGELO_OT_enhancer_settings, ANGELO_OT_studio_asset,
    ANGELO_OT_studio_asset_action, ANGELO_OT_studio_export, ANGELO_OT_studio_import_3d, asset_folders,
    draw_enhancer_settings, enhancer_account_changed, enhancer_accounts, enhancer_models)
from .studio_library_ops import classes as LIBRARY_CLASSES  # noqa: E402
from .media_drop import classes as MEDIA_CLASSES  # noqa: E402


CLASSES = (ANGELO_OT_studio_input, ANGELO_OT_studio_files, ANGELO_FH_studio, ANGELO_OT_studio_url,
           ANGELO_OT_studio_folder, ANGELO_OT_studio_prompt, ANGELO_OT_studio_generate, ANGELO_OT_studio_enhance,
           ANGELO_OT_enhancer_settings, ANGELO_OT_enhancer_refresh, ANGELO_OT_studio_asset, ANGELO_OT_studio_asset_action,
           ANGELO_OT_studio_import_3d, ANGELO_OT_studio_export) + LIBRARY_CLASSES + MEDIA_CLASSES


def register():
    global _enabled, _handler, _image_shader, _input_generation
    _input_generation += 1
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    from . import media_menus, studio_item_menu
    media_menus.register()
    studio_item_menu.register()   # The grid's right-click menu (1.0.5).
    _enabled = True
    studio_views.on_load(cancel_requests)
    if not bpy.app.background:
        _image_shader = gpu.shader.from_builtin('IMAGE')
        _handler = bpy.types.SpaceConsole.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
        bpy.app.timers.register(pump, first_interval=.1, persistent=True)   # A file load must not stop it.


def unregister():
    global _enabled, _handler, _library, _jobs, _references
    _enabled = False
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    if _handler:
        bpy.types.SpaceConsole.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    if _jobs:
        _jobs.close()
    for cancel in _enhancements.values():
        cancel.set()
    _enhancements.clear()
    _enhancement_connections.clear()
    for entry in _textures.values():
        release_preview(entry, bpy.data.images)
    _textures.clear()
    from . import media_menus, studio_item_menu
    studio_item_menu.unregister()
    media_menus.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _enum_cache.clear()
    studio_views.clear(_views)
    _modals.clear()
    _library = _jobs = _references = None
