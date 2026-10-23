# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Asset Shelf that shows nothing says so, and offers Media Studio (1.0.5).

Evidence: the welcome's "Show my assets" on a fresh profile opened Object mode's shelf as an empty strip with only
"All" and Search, which read as broken (Part F's run 3, 2026-10-23). The shelf's count comes from Angelo's RNA
(``AssetShelf.angelo_shown_count``: the assets its last build listed after its asset poll and catalog tab, before the
name search; ``angelo_list_ready``: its library has finished loading), so the state shows only when it is true, never
while the list is still being read, and never on a build without the count.

One line, centred in the shelf (it is one row tall): Media Studio's spark-frame glyph, "Your generated images and
models appear here" in ``MUTED``, and a teal **Generate in Media Studio** (the Asset Browser's empty-state button,
``asset_browser_states``), which opens Media Studio on the shelf's tab through the shared opener. The shelf's own
Media Studio and Generation Flows pills (``asset_shelf_add``) stay, as in every state. Where the line does not fit, the
message goes and the button stays. ``asset_shelf_add``'s input handler takes the button's clicks (``hit``).

Only Angelo Media Studio's own view says this (``shows_studio``): Angelo's Object-mode shelf on the Angelo Media
Studio tab (or a catalog under it), from every library or that library alone, or on that library's All tab. Any other
empty shelf (a brush shelf, a person's own catalog, Current File, another library) shows no Angelo state at all and
keeps Blender's own empty shelf: there, generating in Media Studio would not fill it.
"""

import bpy

from . import card_kit as kit, media_shelf, ui_tokens as tk
from .ui_text import iface_, n_

MESSAGE = n_("Your generated images and models appear here")
GENERATE = n_("Generate in Media Studio")
GLYPH, GAP, PAD, TEXT, BUTTON_TEXT = 20.0, 10.0, 14.0, 13.0, 12.0
places = {}   # area pointer -> (window pointer, button rect in window pixels) as last drawn.
hovered = set()   # Area pointers whose button the pointer is over.
texts = {}   # area pointer -> whether its message drew beside the button (False: too narrow), as last drawn.
_handlers = []


def empty(shelf):
    """Whether ``shelf`` (an AssetShelf) has finished loading and lists nothing; False on a build without the count."""
    if shelf is None or not getattr(shelf, "angelo_list_ready", False):
        return False
    return getattr(shelf, "angelo_shown_count", -1) == 0


def shows_studio(shelf_idname, library, tab, studio_library, studio_paths):
    """Whether a shelf (its ``bl_idname``, ``asset_library_reference`` and ``active_catalog_path``) shows Angelo Media
    Studio's assets: Angelo's shelf on a ``studio_paths`` catalog (or under one) from every library or the Angelo Media
    Studio library (``studio_library``, its name in the Preferences), or on that library's All tab. Pure."""
    if shelf_idname != media_shelf.ANGELO_AST_object_assets.__name__ or not studio_library:
        return False
    if not tab:
        return library == studio_library
    under = any(path and (tab == path or tab.startswith(path + "/")) for path in studio_paths)
    return under and library in ('ALL', studio_library)


def _studio_view(shelf):
    paths = [media_shelf.studio_tab_path(), *media_shelf.studio_catalogs().values()]
    return shows_studio(getattr(shelf, "bl_idname", ""), getattr(shelf, "asset_library_reference", ""),
                        getattr(shelf, "active_catalog_path", ""), media_shelf.library_name(), paths)


def layout(width, height, text_width, button_text_width, scale):
    """``(glyph x, text x or None, button rect)`` in region pixels, the line centred; the message is dropped where it
    does not fit beside the button. Pure."""
    button_w = button_text_width + 2 * PAD * scale
    button_h = tk.BUTTON_HEIGHT_SMALL * scale
    full = GLYPH * scale + GAP * scale + text_width + GAP * scale + button_w
    show_text = full <= width - 2 * PAD * scale
    line = full if show_text else GLYPH * scale + GAP * scale + button_w
    x = max(PAD * scale, (width - line) / 2)
    glyph_x = x
    text_x = x + (GLYPH + GAP) * scale if show_text else None
    button_x = x + (GLYPH + GAP) * scale + ((text_width + GAP * scale) if show_text else 0)
    return glyph_x, text_x, (button_x, (height - button_h) / 2, button_w, button_h)


def hit(window_pointer, x, y):
    """The area pointer whose empty-state button holds window pixel (``x``, ``y``) in this window, else None."""
    for pointer, (window, rect) in places.items():
        if window == window_pointer and rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
            return pointer
    return None


def _draw():
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if area is None or region is None or window is None or region.type != 'ASSET_SHELF':
        return
    pointer = area.as_pointer()
    shelf = getattr(bpy.context, "asset_shelf", None)
    if not empty(shelf) or region.width <= 1 or region.height <= 1 or not _studio_view(shelf):
        places.pop(pointer, None)
        texts.pop(pointer, None)
        hovered.discard(pointer)
        return
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import asset_shelf_add, canvas_rtl, chat_view as canvas
    try:
        scale = canvas.ui_scale()
        message, label = iface_(MESSAGE), iface_(GENERATE)
        text_w = canvas.measure(message, TEXT, scale=scale) * scale
        label_w = canvas.measure(label, BUTTON_TEXT, scale=scale) * scale
        glyph_x, text_x, rect = layout(region.width, region.height, text_w, label_w, scale)
        places[pointer] = (window.as_pointer(), (region.x + rect[0], region.y + rect[1], rect[2], rect[3]))
        texts[pointer] = text_x is not None
        cy = region.height / 2
        gpu.state.blend_set('ALPHA_PREMULT')
        draw_texture_2d(asset_shelf_add._texture("studio"), (glyph_x, cy - GLYPH * scale / 2), GLYPH * scale,
                        GLYPH * scale)
        gpu.state.blend_set('ALPHA')
        import blf
        font = canvas._font
        if text_x is not None:
            blf.size(font, TEXT * scale)
            blf.position(font, text_x, cy - TEXT * scale * 0.35, 0)
            blf.color(font, *kit.MUTED)
            blf.draw(font, canvas_rtl.visual(message))
        x, y, w, h = rect
        canvas.shape(x, y, w, h, kit.TEAL_HOVER if pointer in hovered else kit.TEAL, h / 2)
        blf.size(font, BUTTON_TEXT * scale)
        blf.position(font, x + PAD * scale, cy - BUTTON_TEXT * scale * 0.35, 0)
        blf.color(font, *kit.INK)
        blf.draw(font, canvas_rtl.visual(label))
    except Exception as error:   # The shelf's own draw never breaks for this.
        print("Angelo: shelf empty state draw:", error)
    finally:
        gpu.state.blend_set('NONE')


def register():
    if not bpy.app.background:
        for name in ("SpaceView3D", "SpaceImageEditor"):
            space = getattr(bpy.types, name)
            _handlers.append((space, space.draw_handler_add(_draw, (), 'ASSET_SHELF', 'POST_PIXEL')))


def unregister():
    for space, handler in _handlers:
        space.draw_handler_remove(handler, 'ASSET_SHELF')
    _handlers.clear()
    places.clear()
    texts.clear()
    hovered.clear()
