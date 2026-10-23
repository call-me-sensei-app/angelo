# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's drawing: its controls (buttons, fields, thumbnails) and its Create, Folders and asset grid panels, the popovers and the expanded prompt.

Split from ``studio.py`` (its 500-line limit); ``studio`` owns the state and re-exports these names."""

import math
from datetime import datetime

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import studio as core   # Rebound state (_image_shader) is read through the module.
from . import canvas_rtl, card_kit as kit, chat_view as canvas
from .studio_previews import preview_alive, release_preview
from .studio_state import ASPECTS, FOLDER_ROWS, KIND_LABELS, OPTION_LABELS, account_line, card_status, model_label, setup_text
from .ui_text import iface_
from .studio import (  # noqa: E402 (studio imports this module after defining these)
    BG, ERROR, FIELD, GREEN, HOVER, LINE, MUTED, PANEL, POPUP_ROW, TOP_ROW_ACTIONS, ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_TEXT, FOCUS, SCRIM, SELECTED, TEXT, _textures, library,
    selected)


def hit(state, rect, action, value=None):
    state["hits"].append((rect, action, value))


def button(state, rect, title, action, value=None, active=False, primary=False, enabled=True, icon=None, size=12):
    x, y, w, h = rect
    hovered = state.get("hover") == (action, value)
    pressed = state.get("pressed") == (action, value)
    color = (ACCENT_PRESSED if pressed else ACCENT_HOVER if hovered else ACCENT) if primary and enabled \
        else SELECTED if active else HOVER if hovered else FIELD
    canvas.shape(x, y, w, h, LINE, 8)
    canvas.shape(x + 1, y + 1, w - 2, h - 2, color, 7)
    foreground = (ACCENT_TEXT if primary else TEXT) if enabled else MUTED
    if icon and not title:   # Icon only (Refresh): the icon centred, and no text at all.
        canvas.icon(icon, x + w / 2, y + h / 2, foreground)
    else:
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
    canvas.shape(x, y, w, h, FOCUS if state["focus"] == key else LINE, 8)
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
    # Scrolled wholly out of its panel: draw no text (a zero-size scissor clips nothing on some GPU back ends, so the
    # placeholder showed below the Create column).
    shown = rows[first:first + visible] if clip_w >= 1 and clip_h >= 1 else []
    for index, (line, offset) in enumerate(shown):
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
        batch = batch_for_shader(core._image_shader, 'TRIS', {"pos": [(x, y), (x + iw, y), (x + iw, y + ih), (x, y + ih)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
        core._image_shader.bind()
        core._image_shader.uniform_sampler("image", texture)
        batch.draw(core._image_shader)
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
        if x <= r[0] < x + w and action not in TOP_ROW_ACTIONS:
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
    footer = library_footer(state, x, y, w)
    old = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x + 2), int(y + 8 + footer), int(w - 4), max(1, int(h - 51 - footer)))
    top = y + h - 78 + state["folder_scroll"]
    for identity, name in records:
        if y + 4 + footer <= top <= y + h - 56:
            count = len(library().visible(state["kind"], identity))
            button(state, (x + 5, top, w - 10, 31), iface_(name) if identity in {"ALL", "UNFILED"} else name, "folder", identity, active=state["folder"] == identity, size=11)
            if identity in {"ALL", "UNFILED"}:
                canvas.text(str(count), x + w - 31, top + 11, 10, MUTED)
        top -= 34
    state["folder_max_scroll"] = max(0, len(records) * 34 - h + 60 + footer)
    gpu.state.scissor_set(*old)
    gpu.state.scissor_test_set(False)


def library_rows():
    """Media Studio's Asset Browser library row, for the Folders column and the folder picker: ``(status text,
    [(button text, action)])``. Off with copies left offers Free up space; its size is measured off the main thread."""
    from . import studio_assets_space as space, studio_library_ops as ops
    on, measured = ops.active(), space.size(ops._directory())
    size = ops.size_text(measured) if measured else ""
    if on:
        return (iface_("On · {size}", size=size) if size else iface_("On")), [(iface_("Turn off"), "library_off")]
    rows = [(iface_("Turn on"), "library_on")]
    if measured:
        rows.append((iface_("Free up space"), "library_free"))
    return (iface_("Off · {size} kept", size=size) if size else iface_("Off")), rows


def library_footer(state, x, y, w):
    """The Folders column's last rows; returns their height."""
    status, actions = library_rows()
    height = 60 + 34 * len(actions)
    canvas.shape(x + 12, y + height - 4, w - 24, 1, LINE)
    label(iface_("ASSET BROWSER"), x + 13, y + height - 26)
    canvas.text(canvas.elide(status, w - 26, 10), x + 13, y + height - 46, 10, MUTED)
    for index, (text, action) in enumerate(actions):
        button(state, (x + 5, y + 8 + 34 * (len(actions) - 1 - index), w - 10, 30), text, action, size=11)
    return height


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
        selected_tile = asset["id"] in (state.get("selected_ids") or ())
        canvas.shape(ax, ay, cw, ch, FOCUS if selected_tile else LINE, 10)
        inset = 2 if selected_tile else 1   # A selected item's ring (Shift or Cmd/Ctrl+click; dragged together).
        canvas.shape(ax + inset, ay + inset, cw - 2 * inset, ch - 2 * inset, PANEL, 10 - inset)
        image_rect = (ax + 2, ay + 88, cw - 4, ch - 90)
        canvas.shape(*image_rect, FIELD, 8)
        ready = asset["status"] == "ready"
        if not thumbnail(asset, image_rect):
            title = card_status(asset)
            canvas.text(canvas.elide(title, cw - 30, 13), ax + 15, ay + ch / 2 + 10, 13, ERROR if asset["status"] == "failed" else MUTED)
            if not ready:
                canvas.shape(ax + 16, ay + ch / 2 - 12, cw - 32, 4, LINE, 2)
                canvas.shape(ax + 16, ay + ch / 2 - 12, (cw - 32) * asset.get("progress", 0) / 100, 4, ACCENT, 2)
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
    canvas.shape(0, 0, width, height, SCRIM)
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
