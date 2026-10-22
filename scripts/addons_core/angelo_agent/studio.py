# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio-style Generative AI editor, rendered with Blender's native GPU.

The library belongs to the app, not to a chat or .blend file. Create/Folders/Grid
occupy separate columns; collapsing Create never overlays the Blender viewport.
Only explicit Generate and Enhance confirmations can incur provider usage.
"""

import math
import queue
import threading
from datetime import datetime
from pathlib import Path

import bpy
import gpu
from bpy.props import StringProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, FileHandler, OperatorFileListElement
from gpu_extras.batch import batch_for_shader

from . import canvas_controls, canvas_rtl, chat_view as canvas, scroll_input
from .studio_state import StudioLibrary, account_line, model_options, studio_layout, error_text, model_label, refusable, setup_text, status_title, card_status, ASPECTS, FOLDER_ROWS, KIND_LABELS, OPTION_LABELS
from .studio_generation import StudioJobs, GenerationError, enhance_gemini, public_url
from .provider_registry import PROVIDER_NAMES, SPECS, is_coding
from .references import ReferenceStore, MIME
from .studio_previews import preview_alive, release_preview
from .ui_text import iface_, n_, plural_, rpt_

language_id = "angelo_genai"
BG = (0.047, 0.028, 0.073, 1)
PANEL = (0.081, 0.047, 0.115, 1)
FIELD = (0.065, 0.038, 0.096, 1)
LINE = (0.17, 0.12, 0.23, 1)
HOVER = (0.15, 0.105, 0.225, 1)
PURPLE = (0.33, 0.25, 0.93, 1)
SELECTED = (0.16, 0.105, 0.31, 1)
TEXT = (0.93, 0.91, 0.97, 1)
MUTED = (0.60, 0.55, 0.68, 1)
GREEN = (0.42, 0.80, 0.63, 1)
POPUP_ROW = 43  # A popover entry's pitch, in pixels.
ERROR = (1.0, 0.55, 0.55, 1)

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
    return _views.setdefault(area.as_pointer(), {"kind": "IMAGE", "collapsed": None, "folder": "ALL",
        "query": "", "prompt": "", "references": [], "models": {}, "aspect": "1:1", "resolution": "2K",
        "quality": "auto", "background": "auto", "size": "", "polycount": "10000", "texture": True,
        "focus": None, "caret": 0, "selection": None, "create_scroll": 0, "grid_scroll": 0,
        "folder_scroll": 0, "notice": "", "enhanced": None, "expanded_prompt": ""})


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
    from .input_policy import interaction_active
    if interaction_active(window):
        raise RuntimeError(n_("Finish or cancel the active Blender tool before opening Generative AI"))
    from .editor_types import select_console
    select_console(window, area, generative=True)
    area.spaces.active.prompt = ""
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region:
        # Console calls banner on every draw while scrollback is empty. Seed
        # both native buffers once so bootstrap cannot steal field focus.
        with bpy.context.temp_override(window=window, area=area, region=region):
            if not area.spaces.active.history:
                bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
            if not area.spaces.active.scrollback:
                bpy.ops.console.scrollback_append(text=iface_("Generative AI · image and 3D studio"), type='INFO')
    library()
    view(area)
    ensure_input(window, area)
    area.tag_redraw()


def banner(context):
    setup_area(context.window, context.area)
    return {'FINISHED'}


def execute(context, is_interactive=False):
    return {'CANCELLED'}  # Enter edits the prompt; generation is explicit.


def autocomplete(context):
    return {'CANCELLED'}


def copy_as_script(context):
    return {'CANCELLED'}


def hit(state, rect, action, value=None):
    state["hits"].append((rect, action, value))


def button(state, rect, title, action, value=None, active=False, primary=False, enabled=True, icon=None, size=12):
    x, y, w, h = rect
    hovered = state.get("hover") == (action, value)
    color = PURPLE if primary and enabled else SELECTED if active else HOVER if hovered else FIELD
    canvas.shape(x, y, w, h, LINE, 8)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, color, 7)
    foreground = TEXT if enabled else MUTED
    tx = x + 12
    if icon:
        canvas.icon(icon, tx + 1, y + h / 2, foreground)
        tx += 18
    canvas.text(canvas.elide(title, w - (tx - x) - 10, size), tx, y + (h - size) / 2 + 1, size, foreground)
    if enabled:
        hit(state, rect, action, value)


def paragraph(value, x, y, width, size=12, color=MUTED, max_rows=3):
    rows = canvas.wrap(str(value), max(16, width), size, canvas._font)
    for index, (line, _) in enumerate(rows[:max_rows]):
        canvas.text(line.rstrip(), x, y - index * (size + 5), size, color)
    return len(rows[:max_rows]) * (size + 5)


def label(value, x, y):
    canvas.text(value, x, y, 11, MUTED)


def field(state, rect, key, placeholder, multiline=False):
    x, y, w, h = rect
    canvas.shape(x, y, w, h, PURPLE if state["focus"] == key else LINE, 8)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, FIELD, 7)
    value = state[key]
    rows = canvas.wrap(value or placeholder, w - 22, 13, canvas._font) if multiline else ((value or placeholder, 0),)
    visible = max(1, int((h - 16) // 19))
    caret = state["caret"] if state["focus"] == key else len(value)
    current = max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)
    first = max(0, current - visible + 1) if state["focus"] == key else 0
    old = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    # The parent Create clip and the field clip must intersect.
    clip_x, clip_y = max(x + 2, old[0]), max(y + 2, old[1])
    clip_w, clip_h = min(x + w - 2, old[0] + old[2]) - clip_x, min(y + h - 2, old[1] + old[3]) - clip_y
    gpu.state.scissor_set(int(clip_x), int(clip_y), max(0, int(clip_w)), max(0, int(clip_h)))
    for index, (line, offset) in enumerate(rows[first:first + visible]):
        py = y + h - 23 - index * 19
        chosen = state.get("selection") if state["focus"] == key else None
        if chosen and value:
            lo, hi = max(chosen[0], offset), min(chosen[1], offset + len(line))
            if hi > lo:
                before = canvas.measure(line[:lo - offset], 13)  # As canvas.text draws the row.
                span = canvas.measure(line[:hi - offset], 13) - before
                canvas.shape(x + 11 + before, py - 3, span, 18, SELECTED, 2)
        canvas.text(line.rstrip(), x + 11, py, 13, TEXT if value else MUTED)
        if state["focus"] == key and value is not None and offset <= caret <= offset + len(line):
            cx = x + 11 + canvas.measure(line[:caret - offset], 13)
            canvas.shape(cx, py - 3, 1, 17, TEXT)
    gpu.state.scissor_set(*old)
    state.setdefault("fields", {})[key] = (rect, rows, first)
    hit(state, rect, "focus", key)


def thumbnail(asset, rect):
    if not asset.get("thumbnail"):
        return False
    key = (asset["id"], asset["thumbnail"])
    entry = _textures.get(key)
    if entry and not preview_alive(entry, bpy.data.images):
        _textures.pop(key, None)
        entry = None  # Undo replaced Main: reload from the durable library.
    if entry is None:
        try:
            path = library().checked_file(asset, asset["thumbnail"])
            # Never reuse an image already loaded in the user's scene. Preview
            # images are private runtime IDs removed when this editor closes.
            image = bpy.data.images.load(str(path), check_existing=False)
            if image.size[0] * image.size[1] > 64 * 1024 * 1024:
                bpy.data.images.remove(image)
                _textures[key] = False
                return False
            if max(image.size) > 768:
                factor = 768 / max(image.size)
                image.scale(max(1, int(image.size[0] * factor)), max(1, int(image.size[1] * factor)))
            texture = gpu.texture.from_image(image)
            entry = (image, texture)
            _textures[key] = entry
            if len(_textures) > 48:
                oldest = next(iter(_textures))
                item = _textures.pop(oldest)
                release_preview(item, bpy.data.images)
        except (ValueError, OSError, RuntimeError):
            _textures[key] = False
            return False
    if not entry:
        return False
    (image, texture), (x, y, w, h) = entry, rect
    # Contain instead of crop: references and generated assets stay inspectable.
    scale = min(w / image.size[0], h / image.size[1])
    iw, ih = image.size[0] * scale, image.size[1] * scale
    x, y = x + (w - iw) / 2, y + (h - ih) / 2
    with canvas_rtl.upright(x, iw) as x:  # Chat cards may be mirrored (right-to-left); a picture never is.
        batch = batch_for_shader(_image_shader, 'TRIS', {"pos": [(x, y), (x + iw, y), (x + iw, y + ih), (x, y + ih)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
        _image_shader.bind()
        _image_shader.uniform_sampler("image", texture)
        batch.draw(_image_shader)
    return True


def create_panel(state, rect):
    x, y, w, h = rect
    canvas.shape(x, y, w, h, LINE, 12)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, PANEL, 11)
    old = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    footer = 92
    gpu.state.scissor_set(int(x + 2), int(y + footer), int(w - 4), max(1, int(h - footer - 2)))
    state["create_clip"] = rect
    px, cw, top = x + 16, w - 32, y + h - 28 + state["create_scroll"]
    label(iface_("MODEL"), px, top)
    model = selected(state)
    top -= 44
    button(state, (px, top, cw, 34), setup_text(model, "name") if model else iface_("Select model"), "model")
    canvas.icon("chevron", px + cw - 15, top + 17, MUTED)
    top -= 20
    label(canvas.elide(account_line(model), cw, 10), px, top)
    if not model or not model["ready"]:
        top -= 40
        button(state, (px, top, cw, 30), iface_("Connect in AI Providers"), "settings", size=11)
    top -= 38
    label(iface_("REFERENCES ({count})", count=len(state["references"])), px, top)
    top -= 40
    button(state, (px, top, 90, 30), iface_("Image"), "reference", icon="plus")
    button(state, (px + 98, top, 65, 30), iface_("URL"), "url")
    for reference in state["references"]:
        top -= 32
        canvas.text(canvas.elide(reference["name"], cw - 24, 11), px + 2, top + 8, 11, TEXT)
        canvas.icon("close", px + cw - 9, top + 12)
        hit(state, (px + cw - 24, top, 24, 24), "remove_reference", reference["id"])
    top -= 22
    label(iface_("Drop images here to add references"), px, top)
    top -= 40
    label(iface_("PROMPT"), px, top)
    button(state, (px + cw - 176, top - 6, 70, 26), iface_("Expand", "Angelo"), "expand", size=10)
    enhancing = state.get("enhancing")
    button(state, (px + cw - 96, top - 6, 96, 26), iface_("Enhancing…") if enhancing else iface_("AI Enhance"), "enhance",
           active=bool(state.get("enhanced")), enabled=bool(state["prompt"].strip()) and not enhancing, size=10)
    top -= 154
    field(state, (px, top, cw, 136), "prompt", iface_("Describe the image you want to generate…") if state["kind"] == "IMAGE" else iface_("Describe the 3D asset you want to generate…"), multiline=True)
    if state.get("enhanced"):
        top -= 26
        label(iface_("Enhanced prompt ready · original preserved"), px, top)
        top -= 38
        button(state, (px, top, 104, 30), iface_("Review & apply"), "review_enhance", size=11)
        button(state, (px + 112, top, 68, 30), iface_("Discard"), "discard_enhance", size=11)
    elif state.get("original_prompt") is not None:
        top -= 36
        button(state, (px, top, cw, 28), iface_("Undo prompt enhancement"), "undo_enhance", size=11)
    top -= 40
    label(iface_("SETTINGS"), px, top)
    mode = model["mode"] if model else ""
    if state["kind"] == "IMAGE":
        top -= 28
        label(iface_("Aspect ratio"), px, top)
        top -= 37
        cell = (cw - 24) / 5
        for index, ratio in enumerate(ASPECTS):
            button(state, (px + index * (cell + 6), top, cell, 30), ratio, "aspect", ratio, active=state["aspect"] == ratio, size=11)
        top -= 29
        label(iface_("Resolution"), px, top)
        top -= 37
        sizes = ("1K", "2K") if mode == "openai_images_api" else ("2K", "4K") if mode == "seedream_api" else ("1K", "2K", "4K")
        if state["resolution"] not in sizes:
            state["resolution"] = sizes[0]
        for index, resolution in enumerate(sizes):
            button(state, (px + index * 55, top, 49, 30), resolution, "resolution", resolution, active=state["resolution"] == resolution, size=11)
        if mode != "gemini_images_api":
            top -= 29
            label(iface_("Output size override"), px, top)
            top -= 40
            field(state, (px, top, cw, 32), "size", iface_("Preset, or 1536x1024"))
        if mode == "openai_images_api":
            top -= 29
            label(iface_("Quality"), px, top)
            top -= 39
            button(state, (px, top, cw, 32), iface_(OPTION_LABELS.get(state["quality"], state["quality"].title())), "quality")
            top -= 29
            label(iface_("Background"), px, top)
            top -= 39
            button(state, (px, top, cw, 32), iface_(OPTION_LABELS.get(state["background"], state["background"].title())), "background")
    else:
        top -= 28
        label(iface_("Target polygon count · triangles"), px, top)
        top -= 40
        field(state, (px, top, cw, 32), "polycount", "10000")
        top -= 41
        button(state, (px, top, cw, 32), iface_("Textures & PBR · On") if state["texture"] else iface_("Textures & PBR · Off"), "texture", active=state["texture"])
        top -= 26
        top -= paragraph(iface_("One reference image or a text prompt. GLB stays in the library until you import it."), px, top, cw, 11, max_rows=3) - 12
        if mode == "meshy_api" and not state["references"] and state["texture"]:
            top -= 28
            top -= paragraph(iface_("Meshy text + texture runs two billable stages: preview, then refine."), px, top, cw, 11, max_rows=3) - 12
    top -= 30
    state["create_max_scroll"] = max(0, y + h + state["create_scroll"] - top - (h - footer) + 16)
    # Keep only hit rectangles that intersect the scrolling panel. A clipped
    # offscreen Generate button must never remain clickable over another panel.
    clipped = []
    for r, action, value in state["hits"]:
        if x <= r[0] < x + w and action not in {"tab", "collapse"}:
            bottom, upper = max(r[1], y + footer), min(r[1] + r[3], y + h - 2)
            if upper > bottom:
                clipped.append(((r[0], bottom, r[2], upper - bottom), action, value))
        else:
            clipped.append((r, action, value))
    state["hits"] = clipped
    gpu.state.scissor_set(*old)
    gpu.state.scissor_test_set(False)
    canvas.shape(x + 1, y + 1, w - 2, footer - 1, PANEL, 11)
    canvas.shape(x + 12, y + footer, w - 24, 1, LINE)
    button(state, (px, y + 35, cw, 40), iface_("Generate image") if state["kind"] == "IMAGE" else iface_("Generate 3D"), "generate",
           primary=True, enabled=bool(state["prompt"].strip() or state["references"]) and bool(model and model["ready"]))
    label(iface_("Uses API credits · confirmation before run") if model and model["ready"] else iface_("Connect a provider to generate"), px, y + 15)
    if state["create_max_scroll"] > 0:
        available = h - footer - 16
        fraction = min(1, available / (available + state["create_max_scroll"]))
        sy = y + h - 8 - fraction * available - state["create_scroll"] / state["create_max_scroll"] * available * (1 - fraction)
        canvas.shape(x + w - 6, sy, 3, fraction * available, LINE, 1)


def folder_panel(state, rect):
    x, y, w, h = rect
    canvas.shape(x, y, w, h, LINE, 12)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, PANEL, 11)
    label(iface_("FOLDERS"), x + 13, y + h - 28)
    canvas.icon("plus", x + w - 21, y + h - 24)
    hit(state, (x + w - 36, y + h - 40, 30, 30), "folder_new")
    records = list(FOLDER_ROWS) + [(r["id"], r["name"]) for r in library().folders.values()]
    old = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x + 2), int(y + 8), int(w - 4), max(1, int(h - 51)))
    top = y + h - 78 + state["folder_scroll"]
    for identity, name in records:
        if y + 4 <= top <= y + h - 56:
            count = len(library().visible(state["kind"], identity))
            button(state, (x + 5, top, w - 10, 31), iface_(name) if identity in {"ALL", "UNFILED"} else name, "folder", identity, active=state["folder"] == identity, size=11)
            if identity in {"ALL", "UNFILED"}:
                canvas.text(str(count), x + w - 31, top + 11, 10, MUTED)
        top -= 34
    state["folder_max_scroll"] = max(0, len(records) * 34 - h + 60)
    gpu.state.scissor_set(*old)
    gpu.state.scissor_test_set(False)


def asset_grid(state, rect):
    x, y, w, h = rect
    header = (x, y + h - 34, w, 32)
    folder_name = iface_("All items") if state["folder"] == "ALL" else iface_("Unfiled") if state["folder"] == "UNFILED" else library().folders.get(state["folder"], {}).get("name", iface_("All items"))
    folder_button = 128 if state["geometry"]["folders"][2] == 0 else 0
    if folder_button:
        button(state, (x, header[1], folder_button, 32), folder_name, "folder_picker", size=11)
    search_width = max(48, w - folder_button - 133 - (8 if folder_button else 0))
    old = gpu.state.scissor_get()
    gpu.state.scissor_set(0, 0, bpy.context.region.width, bpy.context.region.height)
    field(state, (x + folder_button + (8 if folder_button else 0), header[1], search_width, 32), "query", iface_("Search generated content…"))
    button(state, (x + w - 89, header[1], 89, 32), iface_("Import"), "import", icon="plus", size=11)
    button(state, (x + w - 125, header[1], 30, 32), "", "refresh", icon="reset")
    gpu.state.scissor_set(*old)
    records = library().visible(state["kind"], state["folder"], state["query"])
    state["visible_assets"] = [a["id"] for a in records]
    grid_top = y + h - 48
    if not records:
        cy = y + (h - 48) / 2
        # Minimal native icon, not invented sample generation results.
        canvas.shape(x + w / 2 - 28, cy + 32, 56, 56, SELECTED, 16)
        canvas.icon("plus", x + w / 2, cy + 60, TEXT)
        title = iface_("No matching assets") if state["query"] else iface_("Your creative library starts here")
        tw = canvas.measure(title, 16)
        canvas.text(title, x + max(10, (w - tw) / 2), cy + 5, 16, TEXT)
        detail = iface_("Try another search or folder.") if state["query"] else iface_("Generate images and 3D assets, or import your own.")
        tw = canvas.measure(detail, 12)
        canvas.text(canvas.elide(detail, w - 20, 12), x + max(10, (w - tw) / 2), cy - 21, 12, MUTED)
        if state["collapsed"]:
            button(state, (x + max(0, (w - 136) / 2), cy - 76, min(136, w), 34), iface_("Open Create"), "collapse", primary=True)
        state["grid_max_scroll"] = 0
        return
    columns = max(1, int((w + 12) // 208))
    cw = (w - (columns - 1) * 12) / columns
    ch = min(cw * .85, 238) + 91
    rows = math.ceil(len(records) / columns)
    state["grid_max_scroll"] = max(0, rows * (ch + 12) - (h - 50))
    state["grid_scroll"] = min(state["grid_scroll"], state["grid_max_scroll"])
    old = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x), int(y + 2), int(w), max(1, int(h - 49)))
    for index, asset in enumerate(records):
        ax = x + (index % columns) * (cw + 12)
        ay = grid_top - ch - (index // columns) * (ch + 12) + state["grid_scroll"]
        if ay + ch < y or ay > grid_top:
            continue
        canvas.shape(ax, ay, cw, ch, LINE, 10)
        canvas.shape(ax + 1, ay + 1, cw - 2, ch - 2, PANEL, 9)
        image_rect = (ax + 2, ay + 88, cw - 4, ch - 90)
        canvas.shape(*image_rect, FIELD, 8)
        ready = asset["status"] == "ready"
        if not thumbnail(asset, image_rect):
            title = card_status(asset)
            canvas.text(canvas.elide(title, cw - 30, 13), ax + 15, ay + ch / 2 + 10, 13, ERROR if asset["status"] == "failed" else MUTED)
            if not ready:
                canvas.shape(ax + 16, ay + ch / 2 - 12, cw - 32, 4, LINE, 2)
                canvas.shape(ax + 16, ay + ch / 2 - 12, (cw - 32) * asset.get("progress", 0) / 100, 4, PURPLE, 2)
        canvas.shape(ax + 9, ay + ch - 27, 45, 18, SELECTED, 5)
        canvas.text(canvas.elide(iface_(KIND_LABELS.get(asset["kind"], asset["kind"])), 36, 9), ax + 15, ay + ch - 22, 9, TEXT)
        paragraph(asset["prompt"], ax + 11, ay + 64, cw - 22, 11, TEXT, 2)
        canvas.text(canvas.elide(model_label(asset["model"]), cw - 22, 10), ax + 11, ay + 21, 10, MUTED)
        date = datetime.fromtimestamp(asset["created"]).strftime("%m/%d/%Y")
        canvas.text(date, ax + 11, ay + 7, 9, MUTED)
        hit(state, (ax, max(ay, y + 2), cw, min(ay + ch, grid_top) - max(ay, y + 2)), "asset", asset["id"])
    gpu.state.scissor_set(*old)
    gpu.state.scissor_test_set(False)


def popup(state, width, height):
    value = state.get("popover")
    if not value:
        return
    entries = value["entries"]
    w = min(328, width - 24)
    h = min(height - 24, 64 + min(10, len(entries)) * POPUP_ROW)
    x = min(max(12, value["anchor"][0]), width - w - 12)
    y = max(12, min(value["anchor"][1] - h, height - h - 12))
    canvas.shape(x - 3, y - 3, w + 6, h + 6, BG, 13)
    canvas.shape(x, y, w, h, LINE, 11)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, PANEL, 10)
    canvas.text(iface_(value["title"]), x + 13, y + h - 24, 12, MUTED)
    state["popup_rect"] = (x, y, w, h)
    state["popup_hits"] = []
    start = value.get("scroll", 0)
    visible = max(1, int((h - 44) // POPUP_ROW))
    for index, entry in enumerate(entries[start:start + visible]):
        rect = (x + 6, y + h - 39 - (index + 1) * POPUP_ROW, w - 12, 41)
        if state.get("popup_hover") == entry["value"]:
            canvas.shape(*rect, HOVER, 7)
        canvas.text(canvas.elide(iface_(entry["label"]) if entry.get("ui") else entry["label"], w - 45, 12), x + 15, rect[1] + 23, 12, TEXT)
        if entry.get("description"):
            canvas.text(canvas.elide(entry["description"], w - 32, 10), x + 15, rect[1] + 7, 10, MUTED)
        if entry.get("selected"):
            canvas.icon("check", x + w - 21, rect[1] + 24, GREEN)
        state["popup_hits"].append((rect, entry["value"]))
    if len(entries) > visible:
        label(iface_("Scroll for more"), x + 15, y + 9)


def expanded_prompt(state, width, height):
    if not state.get("prompt_dialog"):
        return
    state["hits"], state["fields"] = [], {}
    w, h = min(800, width - 28), min(650, height - 28)
    x, y = (width - w) / 2, (height - h) / 2
    canvas.shape(0, 0, width, height, (0, 0, 0, .65))
    canvas.shape(x, y, w, h, LINE, 14)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, PANEL, 13)
    enhanced = state["prompt_dialog"].get("enhanced")
    canvas.text(iface_("Review enhanced prompt") if enhanced else iface_("Prompt", "Angelo"), x + 20, y + h - 32, 16, TEXT)
    label(iface_("Original preserved until you apply.") if enhanced else iface_("Enter for a new line · Esc to cancel"), x + 20, y + h - 55)
    old = gpu.state.scissor_get()
    gpu.state.scissor_set(0, 0, width, height)
    field(state, (x + 20, y + 72, w - 40, h - 148), "expanded_prompt", iface_("Describe what you want to create…"), multiline=True)
    gpu.state.scissor_set(*old)
    button(state, (x + w - 231, y + 20, 94, 34), iface_("Cancel"), "prompt_cancel")
    button(state, (x + w - 127, y + 20, 107, 34), iface_("Apply prompt"), "prompt_apply", primary=True)
    state["prompt_dialog"]["rect"] = (x, y, w, h)


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
            canvas.text(iface_("Generative AI · widen this editor"), 12, max(10, height - 24), 11, TEXT)
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
        if width > 600:
            canvas.text(iface_("Local media library"), width - 144, height - 34, 11, MUTED)
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
        print("Angelo Generative AI draw:", error)
        gpu.state.scissor_test_set(False)
        gpu.state.blend_set('NONE')


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
    elif action == "clear_notice":
        state["notice"] = ""
    redraw()


def attach(state, paths):
    if any(Path(p).suffix.lower() not in MIME for p in paths):
        raise ValueError(n_("Generative AI references must be PNG, JPEG or WebP images"))
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


class ANGELO_OT_studio_input(Operator):
    bl_idname = "angelo.studio_input"
    bl_label = "Generative AI Input"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.window_pointer = context.window.as_pointer()
        self.input_generation = _input_generation
        self.focus_area = None
        self.dragging = False
        _modals.add(self.window_pointer)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if not _enabled or getattr(self, "input_generation", _input_generation) != _input_generation:
            if getattr(self, "input_generation", _input_generation) == _input_generation:
                _modals.discard(self.window_pointer)
            return {'CANCELLED', 'PASS_THROUGH'}  # A retired handler: its last event belongs to what is below.
        window = context.window
        if not window or window.as_pointer() != self.window_pointer:
            return {'PASS_THROUGH'}
        from .input_policy import canvas_blocking, overlay_passes
        if canvas_blocking(window):  # Angelo's own overlays (the welcome, cards) leave this canvas usable.
            self.dragging = False
            return {'PASS_THROUGH'}
        # Mouse release can occur in another editor or outside the window.
        # End selection drag before applying any region/focus filtering.
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
            return {'PASS_THROUGH'}
        mouse = 'MOUSE' in event.type or event.type in {'TRACKPADPAN', 'TRACKPADZOOM', 'EVT_DROP'}
        area = next((a for a in window.screen.areas if is_studio(a) and a.x <= event.mouse_x < a.x + a.width
                     and a.y <= event.mouse_y < a.y + a.height), None)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not area:
            self.focus_area = None
            for state in _views.values():
                state["focus"] = None
                state.pop("popover", None)
            redraw()
        if area is None and not mouse and self.focus_area:
            area = next((a for a in window.screen.areas if is_studio(a) and a.as_pointer() == self.focus_area), None)
        if not area or overlay_passes(event, context.window_manager):   # Timers, window events, the quit key.
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'WINDOW_DEACTIVATE'}:
                self.dragging = False
            return {'PASS_THROUGH'}
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if not region or (mouse and not (region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height)):
            return {'PASS_THROUGH'}
        state, x, y = view(area), event.mouse_x - region.x, event.mouse_y - region.y
        if event.type == 'EVT_DROP':
            defer_drop(window, area)
            return {'PASS_THROUGH'}
        popup_state = state.get("popover")
        if popup_state:
            if event.type in scroll_input.SCROLL_EVENTS:
                # Whole rows from the top; a trackpad swipe carries its remainder to its next event, never past an end.
                rest = popup_state.get("scroll_rest", 0.0) if event.type == 'TRACKPADPAN' else 0.0
                travel = rest - scroll_input.view_up(event, POPUP_ROW)
                rows = int(travel / POPUP_ROW)
                limit = max(0, len(popup_state["entries"]) - 4)
                scroll = max(0, min(limit, popup_state["scroll"] + rows))
                popup_state["scroll_rest"] = travel - rows * POPUP_ROW if scroll == popup_state["scroll"] + rows else 0.0
                popup_state["scroll"] = scroll
            elif event.type == 'ESC' and event.value == 'PRESS':
                state.pop("popover", None)
            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                choice = next((v for r, v in state.get("popup_hits", []) if canvas.inside(r, x, y)), None)
                if choice is not None:
                    action = popup_state["action"]
                    if action == "model":
                        state["models"][state["kind"]] = choice
                    elif action == "folder_picker":
                        state.update(folder=choice, grid_scroll=0)
                    else:
                        state[action] = choice
                state.pop("popover", None)
            elif event.type == 'MOUSEMOVE':
                state["popup_hover"] = next((v for r, v in state.get("popup_hits", []) if canvas.inside(r, x, y)), None)
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            if self.dragging and state.get("focus"):
                key = state["focus"]
                caret = point_to_caret(state, key, x, y)
                state["caret"] = caret
                state["selection"] = tuple(sorted((state.get("anchor", caret), caret)))
                area.tag_redraw()
                return {'RUNNING_MODAL'}
            if canvas_controls.pointer(window, area.tag_redraw, state, x, y, state.get("hits", [])[::-1]):
                return {'RUNNING_MODAL'}  # Keeps the hand: unconsumed, the console restores its text cursor.
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
            return {'PASS_THROUGH'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'} and not state.get("prompt_dialog"):
            step = -scroll_input.view_up(event, 48)  # Measured down from each column's top.
            for column, key in (("create", "create_scroll"), ("folders", "folder_scroll"), ("assets", "grid_scroll")):
                if canvas.inside(state.get("geometry", {}).get(column, (0, 0, 0, 0)), x, y):
                    state[key] = max(0, min(state.get(key.replace("_scroll", "_max_scroll"), 0), state[key] + step))
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.focus_area = area.as_pointer()
            target = next(((r, a, v) for r, a, v in reversed(state.get("hits", [])) if canvas.inside(r, x, y)), None)
            state["focus"] = None
            if target:
                rect, action, value = target
                with context.temp_override(window=window, area=area, region=region):
                    refusable(state, lambda: click(bpy.context, state, action, value, rect))
                if action == "focus":
                    state["caret"] = point_to_caret(state, value, x, y)
                    state["anchor"] = state["caret"]
                    self.dragging = True
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        # Native popup/file-picker dialogs own their keyboard events. The
        # studio relinquishes focus before invoking any native modal dialog.
        key = state.get("focus")
        if not key or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        modifier = event.ctrl or event.oskey
        value, caret = state[key], state["caret"]
        chosen, insert = state.get("selection"), None
        if modifier and event.type == 'A':
            state["selection"], state["caret"] = (0, len(value)), len(value)
        elif modifier and event.type in {'C', 'X'}:
            if chosen:
                context.window_manager.clipboard = value[chosen[0]:chosen[1]]
                if event.type == 'X':
                    value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
                    state["selection"] = None
        elif modifier and event.type == 'V':
            insert = context.window_manager.clipboard.replace("\r\n", "\n")
        elif event.type in {'BACK_SPACE', 'DEL'}:
            if chosen:
                value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
            elif event.type == 'BACK_SPACE' and caret:
                value, caret = value[:caret - 1] + value[caret:], caret - 1
            elif event.type == 'DEL':
                value = value[:caret] + value[caret + 1:]
            state["selection"] = None
        elif event.type in {'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
            previous = caret
            if event.type in {'UP_ARROW', 'DOWN_ARROW'}:
                rows = state.get("fields", {}).get(key, (None, ((value, 0),), 0))[1]
                index = max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)
                target = max(0, min(len(rows) - 1, index + (-1 if event.type == 'UP_ARROW' else 1)))
                caret = rows[target][1] + min(len(rows[target][0]), caret - rows[index][1])
            else:
                caret = max(0, caret - 1) if event.type == 'LEFT_ARROW' else min(len(value), caret + 1) if event.type == 'RIGHT_ARROW' else 0 if event.type == 'HOME' else len(value)
            state["selection"] = tuple(sorted((chosen[0] if chosen else previous, caret))) if event.shift else None
        elif event.type in {'RET', 'NUMPAD_ENTER'}:
            if key in {"prompt", "expanded_prompt"}:
                insert = "\n"
            else:
                state["focus"] = None
        elif event.type == 'ESC':
            state["focus"], state["selection"] = None, None
            state.pop("prompt_dialog", None)
        elif event.unicode and not modifier and not event.alt:
            insert = event.unicode
        else:
            return {'RUNNING_MODAL'}  # Never execute a studio prompt as Python.
        if insert is not None:
            if chosen:
                value, caret = value[:chosen[0]] + value[chosen[1]:], chosen[0]
            if key not in {"prompt", "expanded_prompt"}:
                insert = insert.replace("\n", " ")
            value, caret = value[:caret] + insert + value[caret:], caret + len(insert)
            state["selection"] = None
        state[key] = value[:16000 if key in {"prompt", "expanded_prompt"} else 200]
        state["caret"] = min(caret, len(state[key]))
        area.tag_redraw()
        return {'RUNNING_MODAL'}


def ensure_input(window, area):
    from .input_policy import canvas_blocking, handler_running
    if canvas_blocking(window):
        return
    if (window.as_pointer() not in _modals or not handler_running(window, "angelo.studio_input")) and not bpy.app.background:
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.angelo.studio_input('INVOKE_DEFAULT')


def point_to_caret(state, key, x, y):
    value = state[key]
    rect, rows, first = state.get("fields", {}).get(key, ((0, 0, 0, 0), ((value, 0),), 0))
    index = max(0, min(len(rows) - 1, first + int((rect[1] + rect[3] - 5 - y) / 19)))
    line, offset = rows[index]
    caret = min(range(len(line) + 1), key=lambda i: abs(rect[0] + 11 + canvas.measure(line[:i], 13) - x))
    return min(len(value), offset + caret)


def state_for(pointer):
    state = _views.get(int(pointer))
    if state is None:
        raise ValueError(n_("The Generative AI editor has closed"))
    state["focus"] = None
    return state


class ANGELO_OT_studio_files(Operator):
    bl_idname = "angelo.studio_files"
    bl_label = "Add Images"
    purpose: EnumProperty(items=[("REFERENCE", "References", ""), ("IMPORT", "Import to library", "")])
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')
    filepath: StringProperty(subtype='FILE_PATH')
    files: CollectionProperty(type=OperatorFileListElement)
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.webp", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.area_pointer:
            self.area_pointer = str(context.area.as_pointer())
        state_for(self.area_pointer)
        if self.filepath or self.files:
            return self.execute(context)  # Native OS/FileHandler drop.
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            state = state_for(self.area_pointer)
            paths = [str(Path(self.directory) / f.name) for f in self.files] or [self.filepath]
            if self.purpose == "REFERENCE":
                attach(state, paths)
            else:
                folder = "" if state["folder"] in {"ALL", "UNFILED"} else state["folder"]
                for path in paths:
                    library().import_image(path, folder)
                state["notice"] = n_("Images imported to the local library. No provider was called.")
            redraw()
            return {'FINISHED'}
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_FH_studio(FileHandler):
    bl_idname = "ANGELO_FH_studio"
    bl_label = "Generative AI References"
    bl_import_operator = "angelo.studio_files"
    bl_file_extensions = ".png;.jpg;.jpeg;.webp"  # i18n: file extension filter, not text

    @classmethod
    def poll_drop(cls, context):
        return is_studio(context.area)


class ANGELO_OT_studio_url(Operator):
    bl_idname = "angelo.studio_url"
    bl_label = "Add Image URL"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    url: StringProperty(name="Image URL")

    def invoke(self, context, event):
        state_for(self.area_pointer)
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        self.layout.prop(self, "url", text="")
        self.layout.label(text="Public HTTPS image link. Downloaded only when you generate.")

    def execute(self, context):
        try:
            parsed = public_url(self.url.strip())
            state = state_for(self.area_pointer)
            if len(state["references"]) >= 4:
                raise ValueError(n_("Use up to four references"))
            import uuid
            state["references"].append({"id": str(uuid.uuid4()), "url": self.url.strip(), "name": parsed.hostname + parsed.path[:50]})
            redraw()
            return {'FINISHED'}
        except (ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_folder(Operator):
    bl_idname = "angelo.studio_folder"
    bl_label = "New Folder"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    name: StringProperty(name="Folder name")

    def invoke(self, context, event):
        state_for(self.area_pointer)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        try:
            folder = library().folder(self.name)
            state_for(self.area_pointer).update(folder=folder["id"], grid_scroll=0)
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_prompt(Operator):
    bl_idname = "angelo.studio_prompt"
    bl_label = "Prompt Editor"
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    prompt: StringProperty(name="Prompt", translation_context="Angelo")
    enhanced: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        state = state_for(self.area_pointer)
        state.update(prompt_dialog={"enhanced": self.enhanced}, expanded_prompt=self.prompt,
                     focus="expanded_prompt", caret=len(self.prompt), selection=None)
        redraw()
        return {'FINISHED'}

    def draw(self, context):
        self.layout.label(text="Improved prompt · original kept for Undo" if self.enhanced else "Generation prompt")
        self.layout.prop(self, "prompt", text="")
        paragraph_lines = self.prompt.splitlines() or [""]
        for line in paragraph_lines[:14]:
            self.layout.label(text=line[:85])

    def execute(self, context):
        state = state_for(self.area_pointer)
        if self.enhanced:
            state["original_prompt"] = state["prompt"]
            state["enhanced"] = None
        state["prompt"] = self.prompt[:16000]
        redraw()
        return {'FINISHED'}


class ANGELO_OT_studio_generate(Operator):
    bl_idname = "angelo.studio_generate"
    bl_label = "Generate with API Credits"
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import ui
        try:
            state = state_for(self.area_pointer)
            model = selected(state)
            if not model or not model["ready"]:
                raise ValueError(n_("Connect this model's API account in AI Providers"))
            if not state["prompt"].strip() and (state["kind"] == "IMAGE" or not state["references"]):
                raise ValueError(n_("Describe what you want to generate"))
            ui.generation_binding(model["connection"])
            from .studio_generation import validate_generation
            validate_generation(model["mode"], model["id"], state["prompt"], state, state["references"])
            self._snapshot = {k: state[k] for k in ("kind", "prompt", "aspect", "resolution", "quality", "background", "size", "polycount", "texture", "folder")}
            self._snapshot["references"] = [dict(r) for r in state["references"]]
            self._model = dict(model)
            return context.window_manager.invoke_props_dialog(self, width=440, confirm_text=iface_("Generate"))
        except (ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}

    def draw(self, context):
        model, state = self._model, self._snapshot
        self.layout.label(text=iface_("{model} · {account}", model=model["name"], account=model["account"]), translate=False)
        self.layout.label(text="Uses your team's provider key through Angelo." if model.get("team") else "Billed to this API account, not your coding subscription.")
        self.layout.label(text=plural_(len(state["references"]), "Upload {count} image reference. One output requested.", "Upload {count} image references. One output requested."), translate=False)
        if model["mode"] == "meshy_api" and state["texture"] and not state["references"]:
            self.layout.label(text="Two billable stages: geometry preview + textured refine.", icon='INFO')
        if not model["verified"] and not model.get("team"):
            self.layout.label(text="Key/model access has not been verified yet.", icon='ERROR')
        self.layout.label(text="Nothing is inserted into the scene automatically.")

    def execute(self, context):
        from . import ui
        try:
            if not hasattr(self, "_snapshot"):
                raise ValueError(n_("Generate must be confirmed from the Generative AI editor"))
            state, model = self._snapshot, self._model
            # Recheck the exact account/model after confirmation. Don't use the
            # stale key cached in a dialog if the user removed/replaced it.
            binding = ui.generation_binding(model["connection"])
            if len(_jobs.active) >= 4:
                raise ValueError(n_("Four generations are already running"))
            folder = state["folder"] if state["folder"] not in {"ALL", "UNFILED"} else ""
            asset = library().add(state["kind"], state["prompt"].strip(), model["name"], model["connection"], folder,
                mode=model["mode"], model_id=model["id"], options={k: state[k] for k in ("aspect", "resolution", "quality", "background", "size", "polycount", "texture")},
                reference_names=[r["name"] for r in state["references"]])
            _jobs.start(asset, binding, asset["options"], state["references"])
            redraw()
            return {'FINISHED'}
        except (OSError, ValueError, GenerationError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


def enhancement_binding():
    from . import ui
    config = library().enhancer
    identity = config["connection"]
    if identity == "AUTO_GEMINI":
        records = [r for r in ui.generation_connections() if ui.gemini_text_ready(r)]
        if len(records) != 1:
            raise ValueError(n_("Add a Gemini API key in AI Providers") if not records else n_("Choose which Gemini account AI Enhance should use in AI Providers"))
        record = records[0]
    else:
        record = ui.find_connection(identity)
        if not record:
            raise ValueError(n_("The selected enhancer connection is missing. Open AI Providers."))
    if not is_coding(record):
        if not ui.gemini_text_ready(record):
            raise ValueError(n_("Choose Gemini or a connected coding model for AI Enhance"))
    elif not ui.connection_ready(record):
        raise ValueError(n_("Reconnect the selected enhancer in AI Providers"))
    return record, dict(config)


class ANGELO_OT_studio_enhance(Operator):
    bl_idname = "angelo.studio_enhance"
    bl_label = "Enhance Prompt"
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            record, config = enhancement_binding()
            self._name = iface_("{account} · {model}", account=record["name"], model=config["model"])
            state_for(self.area_pointer)
            return context.window_manager.invoke_props_dialog(self, width=420, confirm_text=iface_("Enhance prompt"))
        except ValueError as error:
            state_for(self.area_pointer)["notice"] = str(error)
            redraw()
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text=self._name, translate=False)
        self.layout.label(text="Uses this account's credits or subscription allowance.")
        self.layout.label(text="Text only. Original prompt preserved; review before applying.")

    def execute(self, context):
        from . import ui
        from .studio_enhance import enhance_native
        try:
            state = state_for(self.area_pointer)
            if state.get("enhancing") or not state["prompt"].strip():
                raise ValueError(n_("Enter a prompt and wait for any running enhancement"))
            record, config = enhancement_binding()
            model = selected(state)
            prompt, kind = state["prompt"], state["kind"]
            generation_model = model["name"] if model else kind
            key = ui._memory_keys.get(record["id"], "")
            access = None if is_coding(record) else ui.gemini_text_binding(record)
            settings, models = (ui.connection_settings(record["mode"], record=record), ui.catalogs().entries.get(record["id"], [])) if is_coding(record) else ({}, [])
            cancel = threading.Event()
            _enhancements[self.area_pointer] = cancel
            _enhancement_connections[self.area_pointer] = record["id"]
            state["enhancing"] = True
            pointer, directory, transport = str(self.area_pointer), library().directory, _jobs.transport

            def run():
                try:
                    reply = (enhance_native(directory, record, settings, key, config["model"], models, prompt, kind, generation_model, cancel)
                        if is_coding(record) else enhance_gemini(access, config["model"], prompt, kind, generation_model, transport))
                    result = {"enhanced": reply, "notice": n_("Enhanced prompt ready. Review & apply to replace your draft.")}
                except Exception as error:
                    message = access.redact(str(error)) if access is not None else str(error).replace(key, "[REDACTED]") if key else str(error)
                    result = {"notice": message[:350] or n_("Enhancement failed; original prompt unchanged")}
                if not cancel.is_set():
                    _enhancement_events.put((pointer, result))
            threading.Thread(target=run, daemon=True, name="AngeloPromptEnhance").start()
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


_enhancer_items, _enhancer_model_items, _folder_items = [], [], []


def enhancer_accounts(self, context):
    global _enhancer_items
    from . import ui
    _enhancer_items = [("AUTO_GEMINI", n_("Gemini · shared Nano Banana key"), n_("Use the sole configured Gemini image account; never a separate key"))]
    for record in ui.connection_records().values():
        if record["mode"] in {"gemini_images_api", "gemini_images_team"} or is_coding(record):
            _enhancer_items.append((record["id"], iface_("{account} · {provider}", account=record["name"], provider=PROVIDER_NAMES[record["provider"]]), n_("Use this exact named connection")))
    key = ("enhancer_accounts", tuple(_enhancer_items))
    return _enum_cache.setdefault(key, _enhancer_items)


def enhancer_models(self, context):
    global _enhancer_model_items
    from . import ui
    record = ui.find_connection(self.connection)
    if not record or record["mode"] in {"gemini_images_api", "gemini_images_team"}:
        # Explicit custom text-model field covers future Gemini models without
        # falsely advertising an unverified entitlement or changing keys.
        _enhancer_model_items = [("gemini-3.8-flash", n_("Gemini Flash 3.8 (Default)"), n_("Uses the same Gemini API key as Nano Banana")),
                                 ("CUSTOM", n_("Other Gemini text model…"), n_("Enter its exact API model ID"))]
    else:
        models = ui.catalogs().entries.get(record["id"], [])
        _enhancer_model_items = [(m["id"], m["name"], n_("Connected native model")) for m in models]
        if not _enhancer_model_items:
            _enhancer_model_items = [("UNAVAILABLE", n_("Refresh models in Coding Agent"), n_("No model catalog available yet"))]
    key = ("enhancer_models", tuple(_enhancer_model_items))
    return _enum_cache.setdefault(key, _enhancer_model_items)


def enhancer_account_changed(self, context):
    self.model = enhancer_models(self, context)[0][0]


class ANGELO_OT_enhancer_settings(Operator):
    bl_idname = "angelo.enhancer_settings"
    bl_label = "AI Enhance Settings"
    connection: EnumProperty(name="Connection", items=enhancer_accounts, update=enhancer_account_changed)
    model: EnumProperty(name="Model", items=enhancer_models)
    custom_model: StringProperty(name="Gemini text model ID")

    def invoke(self, context, event):
        config = library().enhancer
        valid = {i[0] for i in enhancer_accounts(self, context)}
        self.connection = config["connection"] if config["connection"] in valid else "AUTO_GEMINI"
        valid_models = {i[0] for i in enhancer_models(self, context)}
        self.model = config["model"] if config["model"] in valid_models else "CUSTOM" if "CUSTOM" in valid_models else next(iter(valid_models))
        self.custom_model = config["model"]
        return context.window_manager.invoke_props_dialog(self, width=510)

    def draw(self, context):
        self.layout.prop(self, "connection")
        self.layout.prop(self, "model")
        self.layout.operator("angelo.enhancer_refresh", text="Refresh connected coding models", icon='FILE_REFRESH')
        if self.model == "CUSTOM":
            self.layout.prop(self, "custom_model")
        self.layout.label(text="Default: Gemini Flash 3.8 · same API key as Nano Banana.")
        self.layout.label(text="No automatic account/billing fallback. Prompts are reviewed before applying.")

    def execute(self, context):
        try:
            model = self.custom_model.strip() if self.model == "CUSTOM" else self.model
            if model == "UNAVAILABLE":
                raise ValueError(n_("Refresh the connected coding models first"))
            library().configure_enhancer(self.connection, model)
            redraw()
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_enhancer_refresh(Operator):
    bl_idname = "angelo.enhancer_refresh"
    bl_label = "Refresh Connected Models"

    def execute(self, context):
        from . import ui
        ui.refresh_connected_models(force=True)
        ui.preferences_ui.redraw()
        return {'FINISHED'}


def draw_enhancer_settings(layout):
    box = layout.box()
    row = box.row()
    row.label(text="AI Enhance", icon='SHADERFX')
    row.operator("angelo.enhancer_settings", text="Configure…")
    config = library().enhancer
    from . import ui
    name = iface_("Gemini · shared Nano Banana key") if config["connection"] == "AUTO_GEMINI" else (ui.find_connection(config["connection"]) or {}).get("name", iface_("Reconnect selected account"))
    box.label(text=iface_("{account} · {model}", account=name, model=config["model"]), translate=False)
    box.label(text="Prompt enhancement only. API keys stay in their existing connections.")


def asset_folders(self, context):
    global _folder_items
    _folder_items = [("UNFILED", n_("Unfiled"), "")] + [(r["id"], r["name"], "") for r in library().folders.values()]
    key = ("folders", tuple(_folder_items))
    return _enum_cache.setdefault(key, _folder_items)


class ANGELO_OT_studio_asset(Operator):
    bl_idname = "angelo.studio_asset"
    bl_label = "Asset Details"
    asset_id: StringProperty(options={'SKIP_SAVE'})
    area_pointer: StringProperty(options={'SKIP_SAVE'})
    folder: EnumProperty(name="Folder", items=asset_folders)

    def invoke(self, context, event):
        asset = library().assets.get(self.asset_id)
        if not asset:
            return {'CANCELLED'}
        state_for(self.area_pointer)
        self.folder = asset.get("folder") or "UNFILED"
        return context.window_manager.invoke_props_dialog(self, width=500, confirm_text=iface_("Save folder"))

    def draw(self, context):
        asset = library().assets[self.asset_id]
        layout = self.layout
        layout.label(text=iface_("{model} · {kind}", model=model_label(asset["model"]), kind=iface_(KIND_LABELS.get(asset["kind"], asset["kind"]))), translate=False)
        layout.label(text=status_title(asset) if asset["status"] in {"ready", "failed", "stopped"} else iface_("{status} · {percent}%", status=status_title(asset), percent=asset.get("progress", 0)), translate=False)
        for line in asset["prompt"].splitlines()[:6]:
            layout.label(text=line[:85])
        if asset.get("error"):
            for line in [text[i:i + 80] for text in (iface_(asset["error"]),) for i in range(0, len(text), 80)]:
                layout.label(text=line, icon='ERROR', translate=False)
        if asset.get("task_id"):
            layout.label(text=iface_("Provider task: {task_id}", task_id=asset["task_id"]), translate=False)
        if asset["status"] == "ready":
            row = layout.row()
            if asset["kind"] == "IMAGE":
                op = row.operator("angelo.studio_asset_action", text="Use as reference", icon='IMAGE')
                op.action, op.asset_id, op.area_pointer = "REFERENCE", self.asset_id, self.area_pointer
            else:
                op = row.operator("angelo.studio_import_3d", text="Import into scene", icon='IMPORT')
                op.asset_id = self.asset_id
            op = row.operator("angelo.studio_export", text="Export…", icon='EXPORT')
            op.asset_id = self.asset_id
        if self.asset_id in _jobs.active:
            op = layout.operator("angelo.studio_asset_action", text="Stop local monitoring")
            op.action, op.asset_id = "STOP", self.asset_id
            layout.label(text="Submitted provider tasks may still finish and be billed.")
        layout.prop(self, "folder")

    def execute(self, context):
        asset = dict(library().assets[self.asset_id], folder="" if self.folder == "UNFILED" else self.folder)
        library().update(asset)
        redraw()
        return {'FINISHED'}


class ANGELO_OT_studio_asset_action(Operator):
    bl_idname = "angelo.studio_asset_action"
    bl_label = "Use Asset"
    action: StringProperty(options={'SKIP_SAVE'})
    asset_id: StringProperty(options={'SKIP_SAVE'})
    area_pointer: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            if self.action == "STOP":
                _jobs.stop(self.asset_id)
            elif self.action == "REFERENCE":
                asset = library().assets[self.asset_id]
                path = library().checked_file(asset, asset["files"][0])
                attach(state_for(self.area_pointer), [str(path)])
            redraw()
            return {'FINISHED'}
        except (ValueError, OSError, KeyError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_import_3d(Operator):
    bl_idname = "angelo.studio_import_3d"
    bl_label = "Import Generated 3D Asset"
    bl_options = {'UNDO'}
    asset_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title=iface_("Import 3D asset into the current scene?"),
            message=iface_("Adds the local GLB with native Blender import. Blender Undo can revert the import."), confirm_text=iface_("Import"))

    def execute(self, context):
        try:
            asset = library().assets[self.asset_id]
            path = library().checked_file(asset, "model.glb")
            from .studio_generation import checked_glb
            checked_glb(path.read_bytes())
            bpy.ops.import_scene.gltf(filepath=str(path))
            self.report({'INFO'}, rpt_("3D asset imported. Use Blender Undo to revert."))
            return {'FINISHED'}
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


class ANGELO_OT_studio_export(Operator):
    bl_idname = "angelo.studio_export"
    bl_label = "Export Asset"
    asset_id: StringProperty(options={'SKIP_SAVE'})
    filepath: StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        asset = library().assets[self.asset_id]
        self.filepath = "Angelo-" + self.asset_id[:8] + Path(asset["files"][0]).suffix
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import shutil
        try:
            asset = library().assets[self.asset_id]
            source = library().checked_file(asset, asset["files"][0])
            target = Path(self.filepath)
            if not target.is_absolute() or target.exists():
                raise ValueError(n_("Choose a new absolute output path; existing files are never overwritten"))
            shutil.copyfile(source, target)
            return {'FINISHED'}
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


def pump():
    if not _enabled:
        return None
    changed = _jobs.pump() if _jobs else False
    while not _enhancement_events.empty():
        pointer, result = _enhancement_events.get_nowait()
        _enhancements.pop(pointer, None)
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
    if changed:
        redraw()
    return .1


CLASSES = (ANGELO_OT_studio_input, ANGELO_OT_studio_files, ANGELO_FH_studio, ANGELO_OT_studio_url,
           ANGELO_OT_studio_folder, ANGELO_OT_studio_prompt, ANGELO_OT_studio_generate, ANGELO_OT_studio_enhance,
           ANGELO_OT_enhancer_settings, ANGELO_OT_enhancer_refresh, ANGELO_OT_studio_asset, ANGELO_OT_studio_asset_action,
           ANGELO_OT_studio_import_3d, ANGELO_OT_studio_export)


def register():
    global _enabled, _handler, _image_shader, _input_generation
    _input_generation += 1
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _enabled = True
    if not bpy.app.background:
        _image_shader = gpu.shader.from_builtin('IMAGE')
        _handler = bpy.types.SpaceConsole.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
        bpy.app.timers.register(pump, first_interval=.1)


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
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _enum_cache.clear()
    _views.clear()
    _modals.clear()
    _library = _jobs = _references = None
