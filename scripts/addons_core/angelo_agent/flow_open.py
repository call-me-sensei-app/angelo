# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open, create or start Generation Flows from anywhere: Director's top bar, the Angelo menu and the composer's + menu.

Before 1.0.5 a flow lived only as a Node Editor tree type, which Director never shows (maintainer: "director mode
basically has no way to get to Media Studio or Generation Flow"). A flow opens in its own window, the way Media Studio
does (``studio_toggle.new_window``): a Node Editor showing the flow, so no layout is rearranged; a flow window this
module opened is reused for the next flow. *New flow* creates an empty one; a template (``flow_starters``) creates
one already wired. Director's Flows opens Angelo's popup (``flow_menu``); the Window and Angelo menus' submenu is
``ANGELO_MT_flows``; both list them and the person's own flows.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator

from . import flow_nodes, flow_starters, flow_tree
from .ui_text import iface_, n_, plural_

MENU_LABEL = n_("Generation Flows")
TOP_BAR_LABEL = n_("Flows")
NEW = n_("New Flow")
TEMPLATES = n_("Start from a template")
YOURS = n_("Your flows")
NOT_OPENED = n_("The flow couldn't open a window here")
LISTED = 12   # Flows listed by name in the menu; the Node Editor's own browser has the rest.
ICON_SIZE = 32
_icons = None
_opened = set()   # Pointers of the flow windows this module opened (this session).


def flow_icon(size=ICON_SIZE, rgb=None):
    """RGBA floats (bottom row first): three nodes, the first linked to the other two. 4x4 supersampled."""
    r, g, b = rgb or (0.93, 0.95, 0.94)   # The glyph's colour (glyph_tint); near-white on the dark bar.
    nodes = ((8.0, 16.0), (24.0, 24.0), (24.0, 8.0))

    def node(x, y):
        return any(abs(x - cx) <= 4.5 and abs(y - cy) <= 4.5 for cx, cy in nodes)

    def wire(x, y):
        (ax, ay) = nodes[0]
        for bx, by in nodes[1:]:
            t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
            if (x - ax - t * (bx - ax)) ** 2 + (y - ay - t * (by - ay)) ** 2 <= 1.3 ** 2:
                return True
        return False

    pixels = []
    for row in range(size):
        for column in range(size):
            hits = sum(node(x, y) or wire(x, y) for x, y in (
                (column + (sx + 0.5) / 4, row + (sy + 0.5) / 4) for sy in range(4) for sx in range(4)))
            alpha = hits / 16
            pixels += (r * alpha, g * alpha, b * alpha, alpha)
    return pixels


def icon_id(rgb=None):
    """Flows' glyph in ``rgb`` (``glyph_tint.colour`` of the widget it sits on)."""
    global _icons
    from . import glyph_tint
    rgb = tuple(rgb or glyph_tint.DEFAULT)
    if _icons is None:
        from bpy.utils import previews
        _icons = previews.new()
    name = glyph_tint.key("flow", rgb)
    if name not in _icons:
        preview = _icons.new(name)
        preview.icon_size = (ICON_SIZE, ICON_SIZE)
        preview.icon_pixels_float = flow_icon(rgb=rgb)
    return _icons[name].icon_id


def flow_trees():
    """The person's flows, by name."""
    return sorted((tree for tree in bpy.data.node_groups if tree.bl_idname == flow_nodes.TREE),
                  key=lambda tree: tree.name.lower())


MARK = "angelo_flow_window"   # Saved on the flow window's screen: a reopened file's flow window is still one.


def marked(window):
    """Whether ``window`` is a flow window, this session's or one saved in the file (its screen carries ``MARK``)."""
    return window.as_pointer() in _opened or bool(window.screen and window.screen.get(MARK))


def flow_window(windows):
    """A flow window (this session's or a reopened file's) that still shows a Generation Flow editor, or None."""
    live = {w.as_pointer(): w for w in windows}
    _opened.update(p for p, w in live.items() if w.screen and w.screen.get(MARK))   # Reopened files' flow windows.
    for pointer in list(_opened):
        window = live.get(pointer)
        area = window.screen.areas[0] if window is not None and window.screen and window.screen.areas else None
        if area is None or area.type != 'NODE_EDITOR' or area.spaces.active.tree_type != flow_nodes.TREE:
            _opened.discard(pointer)
    return next((live[p] for p in _opened), None)


def show(window, tree):
    """Show ``tree`` in this module's flow window, opening one copied from ``window``'s largest editor if needed."""
    from . import studio_toggle
    from . import flow_window as branded, studio_workspaces
    if studio_workspaces.show(window, "flows"):   # Manual: the flow opens on the Generation Flows tab.
        show_on_tab(window, tree.name)
        return window
    target = flow_window(bpy.context.window_manager.windows)
    created = target is None
    if created:
        target = studio_toggle.new_window(window, NOT_OPENED)
        area = target.screen.areas[0]
        with bpy.context.temp_override(window=target, area=area):
            area.ui_type = flow_nodes.TREE
        _opened.add(target.as_pointer())
        target.screen[MARK] = True   # Saved with the file: reopened, it gets its bar back (flow_window.is_flow_window).
    area = target.screen.areas[0]
    area.spaces.active.node_tree = tree
    if created:   # Angelo's bar in place of Blender's node editor chrome (flow_window).
        branded.prepare(target)
    area.tag_redraw()
    frame_when_drawn(target, tree)
    return target


FRAME_TRIES = 60   # Ticks of FRAME_TICK seconds to wait for the window's size and the nodes' drawn sizes.
FRAME_TICK = 0.05


def frame_when_drawn(window, tree):
    """Frame the whole flow in its window (View All, which leaves a margin) once the window has its real size and
    every node has been drawn: a new window's editor and a node's size are known only after a draw, so framing at
    once (1.0.5's first cut) left a template's first node clipped at the left and the graph low in the view."""
    pointer, name, seen = window.as_pointer(), tree.name, []

    def tick():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        area = target.screen.areas[0] if target is not None and target.screen.areas else None
        space = area.spaces.active if area is not None and area.type == 'NODE_EDITOR' else None
        if space is None or space.node_tree is None or space.node_tree.name != name:
            return None   # Closed, or showing another flow, which framed itself.
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        seen.append((region.width, region.height) if region else (0, 0))
        drawn = all(node.dimensions[0] > 0 for node in space.node_tree.nodes)
        if not (drawn and len(seen) > 1 and seen[-1] == seen[-2] and min(seen[-1]) > 1):
            return FRAME_TICK if len(seen) < FRAME_TRIES else None
        with bpy.context.temp_override(window=target, area=area, region=region):
            bpy.ops.node.view_all()
        area.tag_redraw()
        return None
    bpy.app.timers.register(tick, first_interval=FRAME_TICK)


def show_on_tab(window, name, tries=40):
    """Put flow ``name`` on Manual's Generation Flows tab once the switch to it has happened (Blender applies a
    workspace change on the next event-loop pass, so the screen is the old one until then)."""
    from . import flow_window as branded, studio_workspaces
    pointer = window.as_pointer()

    def assign():
        nonlocal tries
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        tree = bpy.data.node_groups.get(name)
        if target is None or tree is None:
            return None
        area = target.screen.areas[0] if target.screen.areas else None
        if not studio_workspaces.active(target, "flows") or area is None or area.type != 'NODE_EDITOR':
            tries -= 1
            return 0.05 if tries > 0 else None
        area.spaces.active.node_tree = tree
        branded.prepare_area(target, area)
        frame_when_drawn(target, tree)
        return None
    bpy.app.timers.register(assign, first_interval=0.01)


def new_flow(window):
    tree = bpy.data.node_groups.new(NEW_NAME, flow_nodes.TREE)   # Canonical; shown translated (flow_label).
    flow_tree.flow_id(tree)
    show(window, tree)
    return tree


def from_starter(window, starter_id):
    """A new flow wired as the template ``starter_id`` (``flow_starters``), shown in the flow window."""
    flow = flow_starters.instantiate(starter_id)
    # Named from the template's title, stored canonical and shown translated (flow_starters.flow_label; review P8).
    tree = bpy.data.node_groups.new(flow_starters.starter(starter_id)["title"], flow_nodes.TREE)
    flow_tree.flow_id(tree)
    flow_tree.write_flow(tree, flow)
    show(window, tree)
    return tree


class ANGELO_OT_flow_open(Operator):
    bl_idname = "angelo.flow_open"
    bl_label = "Open Generation Flow"
    bl_description = "Show this Generation Flow in its own window"
    bl_options = {'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree)
        if tree is None or tree.bl_idname != flow_nodes.TREE or context.window is None:
            return {'CANCELLED'}
        try:
            show(context.window, tree)
        except RuntimeError as error:
            self.report({'ERROR'}, iface_(str(error)))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_flow_create(Operator):
    bl_idname = "angelo.flow_create"
    bl_label = "New Generation Flow"
    bl_description = "Create a Generation Flow, empty or from a template, and show it in its own window"
    bl_options = {'UNDO'}
    starter: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if context.window is None or (self.starter and flow_starters.starter(self.starter) is None):
            return {'CANCELLED'}
        try:
            from_starter(context.window, self.starter) if self.starter else new_flow(context.window)
        except RuntimeError as error:
            self.report({'ERROR'}, iface_(str(error)))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_MT_flows(Menu):
    bl_label = "Generation Flows"

    def draw(self, context):
        layout = self.layout
        layout.operator("angelo.flow_create", text=iface_(NEW), translate=False, icon='ADD')
        layout.separator()
        layout.label(text=iface_(TEMPLATES), translate=False)
        for entry in flow_starters.STARTERS:
            layout.operator("angelo.flow_create", text=iface_(entry["title"]),   # No stock icon: the heading says
                            translate=False).starter = entry["id"]                 # what the row is (spec §2).
        trees = flow_trees()
        if trees:
            layout.separator()
            layout.label(text=iface_(YOURS), translate=False)
            for tree in trees[:LISTED]:
                layout.operator("angelo.flow_open", text=flow_starters.flow_label(tree.name),
                                translate=False).tree = tree.name


def draw_top_bar(layout, context, compact=False):
    """Flows beside Studio in Director's top bar (``director.draw_topbar``), as wide as its label in this language
    (``top_bar_fit``: Blender's own estimate drew it as "Flo…")."""
    from . import flow_menu, studio_workspaces, top_bar_fit
    label = "" if compact else iface_(TOP_BAR_LABEL)   # Icon only when the bar lacks room; the tooltip names it.
    row = top_bar_fit.fitted_row(layout, context, "flows", label)
    from . import glyph_tint
    pressed = (studio_workspaces.active(context.window, "flows")   # Pressed on Manual's Flows tab ...
               or flow_menu.is_open(context.window))   # ... and while Director's Flows popup is open.
    ink = glyph_tint.colour("tool", pressed)   # The glyph in the button's own text colour, in every theme.
    row.operator("angelo.show_flows", text=label, translate=False, icon_value=icon_id(ink),
                 emboss=True, depress=pressed)   # As Chat, Studio.


routes = []   # The routes Flows took this session, newest last (GUI suites read it): tab (Manual), popup (Director).


class ANGELO_OT_show_flows(Operator):
    bl_idname = "angelo.show_flows"
    bl_label = "Generation Flows"
    bl_description = "Generation Flows: go further, chain generation steps into a flow you can rerun"   # F3: not INTERNAL.

    def execute(self, context):
        from . import layout_after, studio_workspaces
        if context.window is None:
            return {'CANCELLED'}
        if layout_after.busy():   # Mid layout change: the window's workspace is transient; act once it settles.
            layout_after.run_after(context.window, open_flows, "flows")
            return {'FINISHED'}
        if studio_workspaces.show(context.window, "flows", toggle=True):   # Manual: the Generation Flows tab.
            routes.append("tab")
            return {'FINISHED'}
        # Director: Angelo's Flows popup under the button or pill that was clicked (studio_entry_points.rst, section
        # 3), never Blender's dropdown; no layout changes, and a hidden chat stays hidden.
        routes.append("popup")
        bpy.ops.angelo.flow_menu('INVOKE_DEFAULT')
        return {'FINISHED'}


def open_flows(window):
    """What the Flows button does, from a timer: Manual's Generation Flows tab, or in Director the Flows popup under
    the top bar's Flows button (``flow_menu.open_from_top_bar``), never at a pointer that is anywhere by then."""
    from . import studio_workspaces
    try:
        from . import flow_menu
    except ImportError:
        flow_menu = None
    anchored = getattr(flow_menu, "open_from_top_bar", None)
    if anchored is not None and not studio_workspaces.in_manual(window):
        routes.append("popup")
        anchored(window)
        return
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.angelo.show_flows()


MENU_WHY = n_("Go further: chain generation steps into a flow you can rerun")
NEW_NAME = flow_starters.NEW_FLOW   # New Flow's name: stored canonical, shown translated (flow_label).
EMPTY = n_("Empty", "Angelo")   # A flow with no steps; the "Angelo" context keeps Blender's object type "Empty" apart.


def size_text(tree):
    """What a flow holds, said under its name: "Empty", or "{count} step(s)"."""
    if not len(tree.nodes):
        return iface_(EMPTY, "Angelo")
    return plural_(len(tree.nodes), "{count} step", "{count} steps")


NEW_ROW, STARTER_ROW, TREE_ROW = "flows:new", "flows:starter:", "flows:tree:"  # i18n: row ids, never shown


def menu_rows(chosen=(), fit_target=""):
    """The Generation Flows rows (``chat_destinations``): New, the templates, then the person's flows. The + menu's
    group, the Flows popup (``flow_menu``) and the start page (``flow_window``) list these. New Flow has a 16 px ADD
    glyph without a tile; a template and one of your flows have none (studio_entry_points.rst, section 2: their
    section says what they are); every title sits in the same column."""
    from .chat_destinations import leaf
    rows = [leaf("flows", NEW_ROW, NEW, glyph="add", operator="angelo.flow_create")]
    rows += [leaf("flows", STARTER_ROW + entry["id"], entry["title"], entry["summary"], glyph=None,
                  operator="angelo.flow_create", properties=(("starter", entry["id"]),))
             for entry in flow_starters.STARTERS]
    # Under one of your flows: its size, not "Your flow" again under the "Your flows" section (review P2).
    rows += [leaf("flows", TREE_ROW + tree.name, flow_starters.flow_label(tree.name), size_text(tree), glyph=None,
                  operator="angelo.flow_open",
                  properties=(("tree", tree.name),), translate=False) for tree in flow_trees()[:LISTED]]
    return rows


CLASSES = (ANGELO_OT_flow_open, ANGELO_OT_flow_create, ANGELO_OT_show_flows, ANGELO_MT_flows)


def register():
    from . import chat_destinations, flow_menu, flow_window, studio_to_flow
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    flow_window.register()
    flow_menu.register()
    studio_to_flow.register()   # Media Studio's Turn into a flow (its pill is drawn by Studio).
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "flows", MENU_LABEL, MENU_WHY, glyph="flow", order=20, section=chat_destinations.CREATE,
        children=menu_rows, owner="flow_open"))


def unregister():
    global _icons
    from . import chat_destinations
    chat_destinations.unregister("flows")
    from . import flow_menu, flow_window, studio_to_flow
    studio_to_flow.unregister()
    flow_menu.unregister()
    flow_window.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _icons is not None:
        from bpy.utils import previews
        previews.remove(_icons)
        _icons = None
    _opened.clear()
