# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open or close Media Studio from Director's top bar or the Angelo menu.

Before 1.0.5 Media Studio was reachable only through the editor-type menu (AI > Media Studio, "Generative AI" before 1.0.5), which Director's
layout never shows, and Generation Flows only as a Node Editor tree type (user feedback, 1.0.4). The **Studio**
button sits beside Chat in Director's top bar and the Angelo menu offers *Media Studio* in both modes.

Media Studio opens in its own window, so Director's 3D view + chat split and a Manual layout are never rearranged:
the largest editor is copied into a new window (Blender's Duplicate Area into New Window, as the chat's Pop Out
does) and that copy becomes the Studio. The button is pressed while a Studio window it opened is open, and a click
then closes that window; a Studio the person placed in their own layout is theirs and never closed from here.
Its icon is drawn here, a picture frame with a spark, filled while open and outlined while closed, like Chat's.

``open_media_studio(kind)`` is the one way another surface opens Media Studio on a tab ("3d" or "image", or None to
keep an open Studio's tab): the Asset Shelf's and Asset Browser's *Generate* and the menus. ``studio_kind(context)`` picks the tab from
where the person is: a surface's look (texture paint, the Image editor, a shader or texture node tree, the Material or
Texture properties, a material, image or texture asset) asks for Image, everything else (objects, meshes, collections,
sculpt) for 3D. The ``angelo.open_media_studio`` operator wraps both for layouts.
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from .ui_text import iface_, n_

# Angelo's own context: Blender's catalogue, consulted first, translates a bare "Studio" (studio lighting) in some
# languages, so the button read 스튜디오 / 攝影棚 while every other entry says Media Studio (1.0.5 review).
LABEL = n_("Studio", "Angelo")


def label():
    """The Studio button's label in the interface language (Angelo's context, never Blender's "Studio")."""
    return iface_(LABEL, "Angelo")


MENU_LABEL = n_("Media Studio")
BACK = n_("Back to {workspace}")
OPEN, CLOSE = n_("Media Studio: generate images and 3D models in one step"), \
    n_("Close the Media Studio window")
NOT_OPENED = n_("Media Studio couldn't open a window here")
TABS = {"3d": "3D", "image": "IMAGE"}   # open_media_studio's kinds -> Media Studio's tabs (studio.view's "kind").
SURFACE_ASSETS = frozenset({'MATERIAL', 'IMAGE', 'TEXTURE'})        # AssetRepresentation.id_type
SURFACE_TREES = frozenset({'ShaderNodeTree', 'TextureNodeTree'})    # SpaceNodeEditor.tree_type
SURFACE_TABS = frozenset({'MATERIAL', 'TEXTURE'})                   # SpaceProperties.context
SURFACE_MODES = frozenset({'PAINT_TEXTURE'})                        # Context.mode
ICON_SIZE = 32
_icons = None
_opened = set()   # Pointers of the windows this module opened as Media Studio (this session).
# Where Media Studio has shown this session: "window" (Director's own window) and "tab" (Manual's tab). AUTO picks the
# tab from where the person came from until that destination first shows (1.0.5 review: one global flag left Manual's
# tab on its default Image tab from Object mode after Director's window had shown, while Director gave 3D).
shown_this_session = set()


def frame_icon(filled, size=ICON_SIZE, rgb=None):
    """RGBA floats (bottom row first): a rounded picture frame with a four-point spark at its upper right, filled
    with the spark cut out while the Studio is open, an outline while closed. 4x4 supersampled, premultiplied."""
    r, g, b = rgb or (0.93, 0.95, 0.94)   # The glyph's colour (glyph_tint); near-white on the dark bar.
    def rounded(x, y, left, right, bottom, top, radius):
        cx = min(max(x, left + radius), right - radius)
        cy = min(max(y, bottom + radius), top - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 and left <= x <= right and bottom <= y <= top

    def frame(x, y):
        return rounded(x, y, 3.5, size - 7.5, 4.0, size - 8.0, 5.0)

    def hollow(x, y):
        return rounded(x, y, 5.7, size - 9.7, 6.2, size - 10.2, 3.0)

    def spark(x, y, cx=size - 7.0, cy=size - 7.0, reach=6.5):
        dx, dy = abs(x - cx), abs(y - cy)
        return dx ** 0.5 + dy ** 0.5 <= reach ** 0.5   # An astroid: four sharp points.

    def gap(x, y):   # The spark's clearance, so it reads apart from the frame's corner.
        return spark(x, y, reach=9.5)

    def hill(x, y):   # A small hill inside the frame: the frame reads as a picture, not a box.
        return y <= 6.2 + 0.55 * (x - 6.0) and y <= 6.2 + 0.55 * (size - 12.0 - x) + 4.0 and y >= 6.2 and x >= 6.0

    pixels = []
    for row in range(size):
        for column in range(size):
            hits = 0
            for sy in range(4):
                for sx in range(4):
                    x, y = column + (sx + 0.5) / 4, row + (sy + 0.5) / 4
                    if spark(x, y):
                        hits += 1
                    elif gap(x, y):
                        continue
                    elif filled:
                        hits += frame(x, y) and not hill(x, y)
                    else:
                        hits += (frame(x, y) and not hollow(x, y)) or (hollow(x, y) and hill(x, y))
            alpha = hits / 16
            pixels += (r * alpha, g * alpha, b * alpha, alpha)
    return pixels


def icon_id(shown, rgb=None):
    """Studio's glyph, filled while shown, in ``rgb`` (``glyph_tint.colour`` of the widget it sits on)."""
    global _icons
    from . import glyph_tint
    rgb = tuple(rgb or glyph_tint.DEFAULT)
    if _icons is None:
        from bpy.utils import previews
        _icons = previews.new()
    name = glyph_tint.key("shown" if shown else "hidden", rgb)
    if name not in _icons:
        preview = _icons.new(name)
        preview.icon_size = (ICON_SIZE, ICON_SIZE)
        preview.icon_pixels_float = frame_icon(shown, rgb=rgb)
    return _icons[name].icon_id


def is_studio(area):
    from .editor_registry import GENERATIVE_AI, editor_identity
    return editor_identity(area) == GENERATIVE_AI


def opened_window(windows):
    """The window this module opened that still shows Media Studio, or None (a closed or repurposed one is forgotten)."""
    live = {w.as_pointer(): w for w in windows}
    for pointer in list(_opened):
        window = live.get(pointer)
        if window is None or window.screen is None or not any(is_studio(a) for a in window.screen.areas):
            _opened.discard(pointer)
    return next((live[p] for p in _opened), None)


def source_area(window):
    """The editor to copy into the new window: the largest 3D view, else the largest editor (never a global bar)."""
    areas = [a for a in window.screen.areas if a.type not in {'TOPBAR', 'STATUSBAR'}]
    views = [a for a in areas if a.type == 'VIEW_3D']
    return max(views or areas, key=lambda a: a.width * a.height, default=None)


def new_window(window, failure=NOT_OPENED):
    """Copy ``window``'s largest editor into a new window (Blender's Duplicate Area into New Window) and return it;
    the caller turns its one area into the editor it opens (Media Studio here, a Generation Flow in ``flow_open``)."""
    area = source_area(window)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if region is None:
        raise RuntimeError(failure)
    windows = bpy.context.window_manager.windows
    before = {w.as_pointer() for w in windows}
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
    created = next((w for w in windows if w.as_pointer() not in before), None)
    if created is None or not created.screen.areas:
        raise RuntimeError(failure)
    return created


def open_studio(window):
    """Media Studio in a new window copied from ``window``'s largest editor. Returns the new window."""
    from .editor_types import mark_angelo_owned, select_console
    created = new_window(window)
    area = created.screen.areas[0]
    select_console(created, area, generative=True)
    mark_angelo_owned(created, area)
    # Its own window shows Media Studio alone, Studio's tabs at the top: never Blender's editor header (the editor-type
    # dropdown, "Media Studio", View), which this window has no use for. Changed with the window in the context, as
    # the welcome opens it from a timer (editor_types.set_editor).
    with bpy.context.temp_override(window=created, screen=created.screen, area=area):
        area.spaces.active.show_region_header = False
    from . import studio, studio_views
    studio_views.forget_window(studio._views, created.as_pointer())   # Fresh, even at a closed window's address.
    _opened.add(created.as_pointer())
    _note_shown("window")
    return created


def _note_shown(destination):
    shown_this_session.add(destination)


def destination(window):
    """Where a general entry shows Media Studio from ``window``: Manual's tab when it has one, else Studio's window."""
    from . import studio_workspaces
    try:
        return "tab" if studio_workspaces.in_manual(window) and studio_workspaces.tab("studio") is not None else "window"
    except AttributeError:   # A stand-in without the tab routing (host tests): the window path.
        return "window"


def kind_for(space_type, mode, asset_type=None, tree_type=None, properties_tab=None):
    """"image" where the person works on a surface's look, else "3d" (``studio_kind``, with plain values). Pure."""
    if (asset_type in SURFACE_ASSETS or space_type == 'IMAGE_EDITOR'
            or (space_type == 'NODE_EDITOR' and tree_type in SURFACE_TREES)
            or (space_type == 'PROPERTIES' and properties_tab in SURFACE_TABS)
            or (space_type == 'VIEW_3D' and mode in SURFACE_MODES)):
        return "image"
    return "3d"


def studio_kind(context):
    """The Media Studio tab ``context`` asks for: "image" or "3d" (``kind_for``). An asset is the active one (the
    Asset Browser's or a shelf's); a brush asset says nothing about the tab, so the editor and mode decide."""
    space = getattr(context, "space_data", None)
    asset = getattr(context, "asset", None)
    return kind_for(getattr(space, "type", None), getattr(context, "mode", None),
                    asset_type=getattr(asset, "id_type", None), tree_type=getattr(space, "tree_type", None),
                    properties_tab=getattr(space, "context", None) if getattr(space, "type", "") == 'PROPERTIES'
                    else None)


def studio_area(window):
    from . import studio
    return next((a for a in window.screen.areas if studio.is_studio(a)), None) if window.screen else None


def open_media_studio(kind, window=None):
    """Media Studio in its own window; returns that window. ``kind`` "3d" or "image" selects that tab (the shelf's
    and browser's Generate); None keeps the tab an open Studio shows (a general entry: AUTO, doc/angelo/
    studio_entry_points.rst, "Behaviour"). ``window`` is the one the person is in (default: the context's); a new
    Studio is copied from it as the top-bar button does (``open_studio``, the one path).

    A Studio window this module opened before is brought forward with what it held (prompt, references, folder, tab):
    Blender cannot raise a window, so it closes and opens again in front, its state moved to the new area. A Studio
    in the person's own layout is left alone. Raises ValueError for another kind and RuntimeError (translatable
    text) when no window can open here."""
    from . import studio, studio_workspaces
    tab = TABS.get(kind) if kind is not None else None
    if kind is not None and tab is None:
        raise ValueError("Media Studio has no {!r} tab (expected 3d or image)".format(kind))
    manual = window or bpy.context.window
    if manual is not None and studio_workspaces.in_manual(manual) and tab is not None:
        _select_tab_before_shown(manual, tab)
    if manual is not None and studio_workspaces.show(manual, "studio"):   # Manual: its own tab (one home per mode).
        _note_shown("tab")
        if tab is not None:
            _select_tab_when_shown(manual, tab)
        return manual
    windows = bpy.context.window_manager.windows
    here = window or bpy.context.window or (windows[0] if len(windows) else None)
    if here is None:
        raise RuntimeError(NOT_OPENED)
    existing = opened_window(windows)
    if existing is not None and existing.as_pointer() == here.as_pointer():
        created = existing   # Asked from inside the Studio window: it is already in front.
    else:
        kept = None
        if existing is not None:
            old = studio_area(existing)
            kept = dict(studio.view(old)) if old else None
            close_studio(existing)
        created = open_studio(here)
        if kept:
            for transient in ("hits", "fields", "geometry", "popover", "prompt_dialog"):
                kept.pop(transient, None)
            studio.view(created.screen.areas[0]).update(kept)
    area = studio_area(created)
    if area is not None:
        if tab is not None:
            studio.click(bpy.context, studio.view(area), "tab", tab, None)   # Studio's own tab click.
        area.tag_redraw()
    from .chat_toggle import redraw_top_bars
    redraw_top_bars()
    return created


def _select_tab_before_shown(window, tab):
    """Select Media Studio's ``tab`` on Manual's Media Studio tab before the switch to it lands, so its first frame
    already shows that tab. Without this, the switch (applied on the next event-loop pass) first drew the tab left
    from the previous visit until ``_select_tab_when_shown`` caught up (the 35f5b544698 slot, 2026-10-23). A screen
    another window shows keeps its own tab."""
    from . import studio, studio_workspaces
    target = studio_workspaces.tab("studio")
    if target is None:
        return
    elsewhere = {w.screen.as_pointer() for w in bpy.context.window_manager.windows
                 if w.screen is not None and w.as_pointer() != window.as_pointer()}
    for screen in target.screens:
        if screen.as_pointer() in elsewhere:
            continue
        for area in screen.areas:
            if is_studio(area):
                studio.click(bpy.context, studio.view(area), "tab", tab, None)
                area.tag_redraw()


def _select_tab_when_shown(window, tab, tries=40):
    """Select Media Studio's ``tab`` on Manual's Media Studio tab once the switch to it has landed (a workspace
    change applies on the next event-loop pass)."""
    from . import studio, studio_workspaces
    pointer = window.as_pointer()

    def select():
        nonlocal tries
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            return None
        area = target.screen.areas[0] if target.screen.areas else None
        if not studio_workspaces.active(target, "studio") or area is None or not is_studio(area):
            tries -= 1
            return 0.05 if tries > 0 else None
        studio.click(bpy.context, studio.view(area), "tab", tab, None)
        area.tag_redraw()
        return None
    bpy.app.timers.register(select, first_interval=0.01)


def close_studio(window):
    from . import studio, studio_views
    studio_views.forget_window(studio._views, window.as_pointer())   # A Studio opened again starts fresh.
    area = window.screen.areas[0]
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.wm.window_close()
    _opened.discard(window.as_pointer())


def draw(layout, context, compact=False):
    """The Studio button beside Chat (``director.draw_topbar``), pressed while its window is open, as wide as its
    label in this language (``top_bar_fit``)."""
    from . import top_bar_fit
    from . import studio_workspaces
    shown = (opened_window(context.window_manager.windows) is not None
             or studio_workspaces.active(context.window, "studio"))
    text = "" if compact else label()   # Icon only when the bar lacks room; the tooltip names it.
    row = top_bar_fit.fitted_row(layout, context, "studio", text)
    from . import glyph_tint
    ink = glyph_tint.colour("tool", shown)   # The glyph in the button's own text colour, in every theme.
    row.operator("angelo.toggle_studio", text=text, translate=False, icon_value=icon_id(shown, ink),
                 depress=shown, emboss=True)


menus_drawn = {}   # Menu idname -> how many times menu_entries drew into it (GUI suites).


def menu_entries(layout, menu):
    """Media Studio and the Generation Flows submenu (New Flow, the templates, your flows: the + menu's registry) as
    menu items: the Angelo menu and Window menu, in both modes (1.0.5: permanent routes besides the top bar and +).
    Media Studio opens through the shared opener when it is registered (``angelo.open_media_studio``)."""
    from . import flow_open, glyph_tint
    ink = glyph_tint.colour("menu_item")
    if hasattr(bpy.types, "ANGELO_OT_open_media_studio"):
        layout.operator("angelo.open_media_studio", text=iface_(MENU_LABEL), translate=False,
                        icon_value=icon_id(False, ink)).kind = 'AUTO'
    else:
        layout.operator("angelo.toggle_studio", text=iface_(MENU_LABEL), translate=False,
                        icon_value=icon_id(False, ink)).mode = 'OPEN'
    layout.menu("ANGELO_MT_flows", text=iface_(flow_open.MENU_LABEL), translate=False,
                icon_value=flow_open.icon_id(ink))
    menus_drawn[menu] = menus_drawn.get(menu, 0) + 1


def menu_item(self, context):
    """Angelo menu > Media Studio and Generation Flows (both modes; Director keeps Blender's menus since 1.0.4)."""
    menu_entries(self.layout, "TOPBAR_MT_blender")
    self.layout.separator()


class ANGELO_OT_toggle_studio(Operator):
    bl_idname = "angelo.toggle_studio"
    bl_label = "Media Studio"
    bl_description = "Open or close Media Studio in its own window"
    bl_options = {'INTERNAL'}
    # TOGGLE: the top-bar button (pressed while open). OPEN: a menu entry, which never closes an open Studio.
    mode: EnumProperty(items=(("TOGGLE", "", ""), ("OPEN", "", "")), default="TOGGLE", options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        from . import studio_workspaces
        if context is None or context.window_manager is None or (properties and properties.mode == 'OPEN'):
            return iface_(OPEN)
        if studio_workspaces.active(context.window, "studio") and studio_workspaces.came_from(context.window):
            return iface_(BACK, workspace=studio_workspaces.came_from(context.window))
        return iface_(CLOSE if opened_window(context.window_manager.windows) else OPEN)

    def execute(self, context):
        from . import layout_after, studio_workspaces
        here = context.window
        if here is None:
            return {'CANCELLED'}
        if layout_after.busy():   # Mid layout change: the window's workspace is transient; act once it settles.
            layout_after.run_after(here, layout_after.again(self.bl_idname, mode=self.mode), "studio")
            return {'FINISHED'}
        # Manual: Media Studio's own tab (a click on the pressed button goes back); Director: its own window.
        if studio_workspaces.show(here, "studio", toggle=self.mode == 'TOGGLE'):
            from .chat_toggle import redraw_top_bars
            redraw_top_bars()
            return {'FINISHED'}
        window = opened_window(context.window_manager.windows)
        try:
            if window is None:
                open_studio(here)
            elif self.mode == 'TOGGLE':
                close_studio(window)
        except RuntimeError as error:
            self.report({'ERROR'}, iface_(str(error)))
            return {'CANCELLED'}
        from .chat_toggle import redraw_top_bars
        redraw_top_bars()
        return {'FINISHED'}


class ANGELO_OT_open_media_studio(Operator):
    """``open_media_studio`` for menus, buttons and F3 search. AUTO (general entries): the tab for where the person
    is (``studio_kind``) the first time Studio shows this session, then the tab it shows; 3D or IMAGE (contextual
    entries) always selects it."""
    bl_idname = "angelo.open_media_studio"
    bl_label = "Media Studio"
    bl_description = "Media Studio: generate images and 3D models in one step"
    # Never drawn as a choice, so its items carry no text.
    kind: EnumProperty(items=(("AUTO", "", ""), ("3D", "", ""), ("IMAGE", "", "")), default="AUTO",
                       options={'SKIP_SAVE'})

    def execute(self, context):
        from . import layout_after
        if context.window is not None and layout_after.busy():   # Mid layout change: act once it settles.
            layout_after.run_after(context.window, layout_after.again(self.bl_idname, kind=self.kind), "studio")
            return {'FINISHED'}
        if self.kind != 'AUTO':
            kind = self.kind.lower()
        else:
            # The context's tab until this destination (Director's window, Manual's tab) first shows this session,
            # taken here before any switch; after that a general entry keeps the tab the person left it on.
            kind = None if destination(context.window) in shown_this_session else studio_kind(context)
        try:
            open_media_studio(kind, context.window)
        except RuntimeError as error:
            self.report({'ERROR'}, iface_(str(error)))
            return {'CANCELLED'}
        return {'FINISHED'}


MENU_WHY = n_("Generate images and 3D models in one step")


def register():
    from . import chat_destinations, flow_open
    bpy.utils.register_class(ANGELO_OT_toggle_studio)
    bpy.utils.register_class(ANGELO_OT_open_media_studio)
    flow_open.register()
    from . import studio_workspaces
    studio_workspaces.register()   # Manual's Media Studio and Generation Flows tabs.
    from . import chrome_guard
    chrome_guard.register()        # Their editors (and a flow's window) keep no stock chrome, by events.
    from . import asset_browser_entry
    asset_browser_entry.register()   # Media Studio and Flows from every Asset Browser.
    bpy.types.TOPBAR_MT_blender.prepend(menu_item)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "media_studio", MENU_LABEL, MENU_WHY, glyph="studio", order=10, section=chat_destinations.CREATE,
        operator="angelo.toggle_studio", properties=(("mode", "OPEN"),), owner="studio_toggle"))


def unregister():
    global _icons
    from . import chat_destinations, flow_open
    chat_destinations.unregister("media_studio")
    bpy.types.TOPBAR_MT_blender.remove(menu_item)
    from . import asset_browser_entry
    asset_browser_entry.unregister()
    from . import studio_workspaces
    studio_workspaces.unregister()
    from . import chrome_guard
    chrome_guard.unregister()
    flow_open.unregister()
    bpy.utils.unregister_class(ANGELO_OT_open_media_studio)
    bpy.utils.unregister_class(ANGELO_OT_toggle_studio)
    if _icons is not None:
        from bpy.utils import previews
        previews.remove(_icons)
        _icons = None
    _opened.clear()
