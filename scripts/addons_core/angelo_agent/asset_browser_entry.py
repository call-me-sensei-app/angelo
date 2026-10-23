# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio and Generation Flows from every Asset Browser (1.0.5, maintainer: a permanent route).

Every Asset Browser, whatever library it shows (Current File, Essentials, a user library, Angelo's own), gets two
pills at the right end of its header, in the welcome's card language (``card_kit`` colours, ``card_draw`` buttons):
**Media Studio** (the shared opener, ``angelo.open_media_studio``: Image for material, image and texture assets, 3D
otherwise) and **Flows** (the Generation Flows rows, ``angelo.show_flows``). They sit in the free span just left of
Blender's search group, never over a Blender item; a narrow header draws them icon only, and one that Blender's own
items already fill draws none (``header_labels``). An empty list or a search without results gets its own state over the
list (``asset_browser_states``, from the browser's real counts); a list with assets gets nothing more. Input is a window-level modal handler per window, as ``flow_window``'s.
"""

import bpy
from bpy.types import Operator

from .ui_text import iface_, n_

STUDIO, FLOWS = n_("Media Studio"), n_("Generation Flows")   # Full names (studio_entry_points.rst rev 3).
H, SIZE, PAD, GAP = 28, 12, 8, 6   # BUTTON_HEIGHT_SMALL, TYPE_SMALL (spec §4).
# Where Blender's own header items sit (FILEBROWSER_HT_header): the editor type and menus at the left, Import
# Settings centred between two spacers (a library other than Current File and Essentials), and the display popover,
# the 8-unit search field, the filter popover and the sidebar toggle at the right. The pills go in the free span left
# of that right group (right of Import Settings when it shows): full labels when they fit, icon only when the icons
# fit, else none (the top bar and Window menu still offer both). A row reserving their width at the header's end was
# given no width by the header layout, so the pills covered the search field and its neighbours (interim dbe022dddb9,
# All Libraries, 1285 px header): the pills never claim Blender's room, they use the room it leaves.
# Blender's own labels, measured as its catalogue translates them (never Angelo messages).
MENUS = ("View", "Select", "Library", "Catalog")   # ASSETBROWSER_MT_editor_menus.
IMPORTS = "Import Settings"   # i18n: Blender's own popover label, measured with its catalogue
LEFT_UNITS = 3.0     # The editor type button, the margins and the separator after the menus.
RIGHT_UNITS = 14.0   # Display popover, 8-unit search, filter popover, sidebar toggle, margins (~13.8 measured).
_handles = []
_hits = {}       # (area pointer, region type) -> [(rect in region pixels, action)]
_drawn = {}      # area pointer -> {"labels": [...], "state": "empty" | "no_results" | None} as last drawn.
_inputs = set()  # Window pointers with a running input handler.
_pointer = {}    # area pointer -> {"hover": action under the pointer, "pressed": action held} for the header pills.


def is_asset_browser(space):
    return space is not None and space.type == 'FILE_BROWSER' and getattr(space, "browse_mode", "") == 'ASSETS'


def _measure(text, size):
    from . import chat_view
    return chat_view.measure(text, size, scale=1.0)


def reserved_width(labels, scale):
    """The width the header reserves for ``labels``' pills: the pills, the gap between them and the padding."""
    return sum(pill_widths(labels, scale)) + (GAP + 2 * PAD) * scale


def blender_spans(space):
    """(left, import settings, right) pixels Blender's own header items take, measured in the interface language:
    each menu is its label plus 0.8 unit (no icon; View to Select is 40 px at scale 1 in interim dbe022dddb9's
    captures), Import Settings its label plus its arrow."""
    from bpy.app.translations import pgettext_iface
    from .top_bar_fit import widget_text
    menus = MENUS + (("Asset",) if hasattr(bpy.types, "ASSETBROWSER_MT_asset") else ())   # Pose Library's menu.
    library = getattr(getattr(space, "params", None), "asset_library_reference", 'LOCAL')
    shows_imports = library not in {'LOCAL', 'ESSENTIALS'}
    widths, unit = widget_text(bpy.context, [pgettext_iface(name) for name in menus + (IMPORTS,)])
    left = LEFT_UNITS * unit + sum(width + 0.8 * unit for width in widths[:-1])
    imports = widths[-1] + 1.75 * unit if shows_imports else 0.0   # Its label and popover arrow.
    return left, imports, RIGHT_UNITS * unit


def header_labels(region_width, scale, space=None):
    """The header pills' labels: the full names when the header holds Blender's own items and the labelled pills
    (measured in the interface language), icon only when it holds the icons, else None (no pills in this header)."""
    full = [(iface_(STUDIO), "studio", "studio"), (iface_(FLOWS), "flows", "flow")]
    icons = [("", "studio", "studio"), ("", "flows", "flow")]
    left, imports, right = blender_spans(space)
    room = region_width - left - right
    if imports:   # Import Settings sits centred in the free span: the pills have the part right of it.
        room = (room - imports) / 2
    narrow = room < reserved_width(full, scale)
    if narrow and room < reserved_width(icons, scale):
        return None
    return [("" if narrow else iface_(STUDIO), "studio", "studio"), ("" if narrow else iface_(FLOWS), "flows", "flow")]


def pill_widths(labels, scale):
    """12 px padding, the 16 px icon, then (with a label) an 8 px gap, the label and 12 px padding."""
    widths = []
    for label, _, _ in labels:
        width = (12 + 16 + 12) * scale
        if label:
            width += 8 * scale + _measure(label, SIZE * scale)
        widths.append(width)
    return widths


def _draw(region_type):
    area, region = bpy.context.area, bpy.context.region
    if area is None or region is None or not is_asset_browser(area.spaces.active):
        return
    import gpu
    from . import chat_view
    scale = chat_view.ui_scale()
    saved = chat_view._scale
    chat_view._scale = 1.0   # Region pixels.
    try:
        hits = []
        if region_type == 'HEADER':
            labels = header_labels(region.width, scale, area.spaces.active) or []
            widths = pill_widths(labels, scale)
            right = blender_spans(area.spaces.active)[2] if labels else 0
            x = region.width - right - PAD * scale - sum(widths) - GAP * scale * (len(widths) - 1)
            # BUTTON_HEIGHT_SMALL is 28 units and a header 26: a pill that tall overhung the header by a unit at the
            # top and bottom (rerun on 6ceaef365ff: "inside" false). It keeps a unit of the header above and below it.
            h = min(H * scale, region.height - 2 * scale)
            y = (region.height - h) / 2
            look = _pointer.get(area.as_pointer(), {})
            for (label, action, icon), width in zip(labels, widths):
                rect = (x, y, width, h)
                pill(rect, label, scale, icon, hover=look.get("hover") == action,
                     pressed=look.get("pressed") == action)
                hits.append((rect, action))
                x += width + GAP * scale
            _drawn.setdefault(area.as_pointer(), {})["labels"] = [label for label, _, _ in labels]
        else:   # The list: an empty or no-results state when there is one, else nothing.
            from . import asset_browser_states
            hits = asset_browser_states.draw(area, region, area.spaces.active, scale)
            _drawn.setdefault(area.as_pointer(), {})["state"] = (asset_browser_states.drawn.get(area.as_pointer()) or
                                                                  {}).get("state")
        _hits[(area.as_pointer(), region_type)] = hits
    finally:
        chat_view._scale = saved
        gpu.state.blend_set('NONE')
    window = bpy.context.window
    if window is not None and window.as_pointer() not in _inputs:
        _start_input(window)


def pill(rect, label, s, icon, hover=False, pressed=False):
    """The entry-point pill (studio_entry_points.rst §4): PANEL fill (PANEL_HOVER under the pointer, TEAL_SOFT over it
    while pressed), a 1 px LINE border, a 16 px icon, then the TEXT label 8 px after it; never a teal fill."""
    from . import card_kit as kit, chat_view
    x, y, w, h = rect
    chat_view.shape(x, y, w, h, kit.LINE, h / 2)
    chat_view.shape(x + s, y + s, w - 2 * s, h - 2 * s, kit.PANEL_HOVER if hover or pressed else kit.PANEL, h / 2 - s)
    if pressed:
        chat_view.shape(x + s, y + s, w - 2 * s, h - 2 * s, kit.TEAL_SOFT, h / 2 - s)
    ix, iy = x + 12 * s, y + (h - 16 * s) / 2
    glyph(icon, ix, iy, 16 * s, kit.TEXT)
    if label:
        chat_view.text(label, ix + 24 * s, y + (h - SIZE * s * 0.72) / 2, SIZE * s, kit.TEXT)


def glyph(icon, x, y, size, colour):
    """Studio's frame with a four-point spark, or Generation Flows' three linked nodes, ``size`` px square."""
    from . import chat_view
    u = size / 16
    if icon == "studio":
        for (ax, ay), (bx, by) in (((1, 2), (13, 2)), ((13, 2), (13, 12)), ((13, 12), (1, 12)), ((1, 12), (1, 2))):
            chat_view.stroke(x + ax * u, y + ay * u, x + bx * u, y + by * u, colour, 1.3 * u)
        for dx, dy in ((0, 3), (3, 0), (0, -3), (-3, 0)):
            chat_view.stroke(x + 13 * u, y + 12 * u, x + (13 + dx) * u, y + (12 + dy) * u, colour, 1.5 * u)
    else:
        for (ax, ay), (bx, by) in (((4, 8), (12, 12)), ((4, 8), (12, 4))):
            chat_view.stroke(x + ax * u, y + ay * u, x + bx * u, y + by * u, colour, 1.2 * u)
        for cx, cy in ((4, 8), (12, 12), (12, 4)):
            chat_view.shape(x + (cx - 2.5) * u, y + (cy - 2.5) * u, 5 * u, 5 * u, colour, 1.5 * u)


library_menu_drawn = []   # The entries each draw of the Library menu added (GUI suites read it).


def draw_library_menu(self, context):
    """Asset Browser ▸ Library: Media Studio and Generation Flows at every browser width. A header too narrow for the
    pills hides them, so this menu is the route that always exists (1.0.5 review SD2)."""
    if not is_asset_browser(getattr(context, "space_data", None)):
        return
    from . import flow_open, glyph_tint, studio_toggle
    layout = self.layout
    layout.separator()
    entries = []
    if hasattr(bpy.types, "ANGELO_OT_open_media_studio"):
        op = layout.operator("angelo.open_media_studio", text=iface_(STUDIO), translate=False,
                             icon_value=studio_toggle.icon_id(False, glyph_tint.colour("menu_item")))
        op.kind = studio_toggle.studio_kind(context).upper()   # The tab for the assets shown, as the pill does.
        entries.append("studio")
    layout.operator("angelo.show_flows", text=iface_(FLOWS), translate=False,
                    icon_value=flow_open.icon_id(glyph_tint.colour("menu_item")))
    entries.append("flows")
    library_menu_drawn.append(entries)


def _start_input(window):
    """A draw callback cannot start an operator: the window's input handler starts on the next tick."""
    pointer = window.as_pointer()
    _inputs.add(pointer)

    def start():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            _inputs.discard(pointer)
            return None
        with bpy.context.temp_override(window=target, screen=target.screen):
            bpy.ops.angelo.asset_browser_input('INVOKE_DEFAULT')
        return None
    bpy.app.timers.register(start, first_interval=0.01)


def act(window, area, action):
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        if action == "flows":
            bpy.ops.angelo.show_flows()
        elif action == "clear_search":
            from . import asset_browser_states
            asset_browser_states.clear_search(area.spaces.active)
            area.tag_redraw()
        elif hasattr(bpy.types, "ANGELO_OT_open_media_studio"):   # Contextual: the tab for the assets shown.
            from .studio_toggle import studio_kind
            bpy.ops.angelo.open_media_studio(kind=studio_kind(bpy.context).upper())
        else:
            bpy.ops.angelo.toggle_studio(mode='OPEN')


def _hit(window, event):
    for area in window.screen.areas:
        if not is_asset_browser(area.spaces.active):
            continue
        for region in area.regions:
            for (x, y, w, h), action in _hits.get((area.as_pointer(), region.type), ()):
                px, py = event.mouse_x - region.x, event.mouse_y - region.y
                if x <= px < x + w and y <= py < y + h and 0 <= px < region.width and 0 <= py < region.height:
                    return area, action
    return None, None


def _hover(window, event):
    """Keep the pointer over each Asset Browser list that draws a state, and the header pill under it; redraw a region
    when the button under the pointer changes."""
    from . import asset_browser_states
    area_hit, action = _hit(window, event)
    for area in window.screen.areas:
        if not is_asset_browser(area.spaces.active):
            continue
        under_now = action if area_hit is not None and area_hit.as_pointer() == area.as_pointer() else None
        look = _pointer.setdefault(area.as_pointer(), {})
        if look.get("hover") != under_now:
            look["hover"] = under_now
            header = next((r for r in area.regions if r.type == 'HEADER'), None)
            if header is not None:
                header.tag_redraw()
    for area in window.screen.areas:
        hits = _hits.get((area.as_pointer(), 'WINDOW'))
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if not hits or region is None:
            asset_browser_states.pointer.pop(area.as_pointer(), None)
            continue

        def under(point):
            return next((action for (x, y, w, h), action in hits
                         if point and x <= point[0] < x + w and y <= point[1] < y + h), None)
        at = (event.mouse_x - region.x, event.mouse_y - region.y)
        was = asset_browser_states.pointer.get(area.as_pointer())
        asset_browser_states.pointer[area.as_pointer()] = at
        if under(at) != under(was):
            region.tag_redraw()


def _press_look(window, area_pointer, action):
    """Show the header pill held down (TEAL_SOFT) while the button is pressed, and redraw its header."""
    _pointer.setdefault(area_pointer, {})["pressed"] = action
    for area in window.screen.areas:
        if area.as_pointer() == area_pointer:
            for region in area.regions:
                region.tag_redraw()


class ANGELO_OT_asset_browser_input(Operator):
    """Clicks on the Asset Browser's Media Studio and Flows pills (internal)."""
    bl_idname = "angelo.asset_browser_input"
    bl_label = "Asset Browser Pills"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.window, self.pressed = context.window.as_pointer(), None
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from . import input_policy
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}   # Timers, window events and the quit key, wherever the pointer is.
        window = next((w for w in context.window_manager.windows if w.as_pointer() == self.window), None)
        if window is None:
            _inputs.discard(self.window)
            return {'FINISHED', 'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE' and context.window is not None and context.window.as_pointer() == self.window:
            _hover(window, event)   # The list states' buttons light up under the pointer.
        if event.type != 'LEFTMOUSE' or context.window is None or context.window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        area, action = _hit(window, event)
        if event.value == 'PRESS' and action:
            self.pressed = (area.as_pointer(), action)
            _press_look(window, self.pressed[0], action)
            return {'RUNNING_MODAL'}
        if event.value == 'RELEASE' and self.pressed:
            pressed, self.pressed = self.pressed, None
            _press_look(window, pressed[0], None)
            if area is not None and (area.as_pointer(), action) == pressed:
                act(window, area, action)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


CLASSES = (ANGELO_OT_asset_browser_input,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.ASSETBROWSER_MT_library.append(draw_library_menu)
    for region_type in ('HEADER', 'WINDOW'):
        _handles.append((bpy.types.SpaceFileBrowser.draw_handler_add(_draw, (region_type,), region_type,
                                                                     'POST_PIXEL'), region_type))


def unregister():
    for handle, region_type in _handles:
        bpy.types.SpaceFileBrowser.draw_handler_remove(handle, region_type)
    _handles.clear()
    bpy.types.ASSETBROWSER_MT_library.remove(draw_library_menu)
    _hits.clear()
    _inputs.clear()
    _pointer.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
