# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Generation Flow's own window or Manual's Generation Flows tab, in Angelo's look: a bar with the flow's name,
Add step and Run all, and a start page while no flow is open.

The window ``flow_open`` opens (Director) and the Generation Flows tab (Manual, ``studio_workspaces``) showed
Blender's stock node editor around the flow (1.0.5 review): a header with View / Select / Add / Node and the
node-group picker, the sidebar's Group / Animation panels and the toolbar. There they are hidden, as Media Studio's
own window hides its header, and this bar (the welcome's card language: ``card_kit`` colours, ``card_draw``
buttons) keeps what a person needs: which flow it is and, under its name, what the hidden sidebar said (its step
count, "Running" with **Stop** while a run is live, or the flow's error in amber); **Add step**, Angelo's dropdown of
the step types (``flow_menu``, ``step_rows``: a tile and a why line each, never Blender's node Add menu), whose row
adds that step at the pointer to be placed, as Shift+A does; **Run all** (``angelo.flow_run``); and the way back,
the **Media Studio** link (studio_entry_points.rst: the shared pill, Media Studio's frame-and-spark glyph). With no flow open the canvas shows the start page (``flow_start``): the Generation Flows rows (New Flow, the
templates, your flows: ``flow_open.menu_rows``, the + menu's registry). A flow shown in a person's own layout keeps
Blender's editor. The bar is drawn for any such editor it finds; input is one window-level modal handler per window,
started the first time a bar draws there, so it survives switching tabs.
"""

import bpy
from bpy.types import Operator

from .input_policy import modal_options
from . import live_windows
from .flow_spec import GENERATOR_TYPES as GENERATORS
from .ui_text import iface_, n_, plural_

ADD = n_("Add step")
RUN = n_("Run all")
STOP = n_("Stop")
STUDIO = n_("Media Studio")
HEIGHT, PAD, BUTTON_H, SIZE, TITLE, STATUS = 48, 14, 30, 13.5, 15, 11.5
PILL_H, PILL_PAD, PILL_ICON, PILL_GAP = 28, 12, 16, 8   # The shared pill (studio_entry_points.rst, section 4).
MIN_TITLE = 120   # Room the flow's name keeps before the pill's label drops.
INPUTS, GENERATE, MORE = n_("Inputs"), n_("Generate"), n_("More steps")
# (node idname suffix, title, why, glyph, section). Titles are the nodes' own labels; the whys are what each adds.
STEP_TYPES = (
    ("ANGELO_FlowImageNode", n_("Image"), n_("A reference picture from your library or a link"), "step_image", INPUTS),
    ("ANGELO_FlowSceneCaptureNode", n_("Scene Capture"), n_("Your scene, rendered as a reference when the flow runs"),
     "step_capture", INPUTS),
    ("ANGELO_FlowTextNode", n_("Text"), n_("Part of a prompt; several can feed one step"), "step_text", INPUTS),
    ("ANGELO_FlowGenerateImageNode", n_("Generate Image"), n_("Make images with a Media Studio model"), "step_image",
     GENERATE),
    ("ANGELO_FlowGenerate3DNode", n_("Generate 3D"), n_("Make a 3D model with a Media Studio model"), "step_model",
     GENERATE),
    ("ANGELO_FlowPlaceNode", n_("Place in Scene"), n_("Bring a generated model into your scene"), "step_place", MORE),
    ("ANGELO_FlowSubflowNode", n_("Subflow"), n_("Run one of your flows as a single step"), "flow", MORE),
)
hints = {}   # Flow name -> (text, node count when given): said under its name until the flow changes.
_handle = None
_bars = {}     # Area pointer -> {"hits": [(rect, action, value)], "hover", "pressed", "drawn"}
_inputs = set()


def step_rows():
    """Add step's rows (``flow_menu``): every step type, in sections, each adding its node at the pointer."""
    from .chat_destinations import leaf
    rows, section = [], None
    for idname, title, why, glyph, here in STEP_TYPES:
        if here != section:
            rows.append({"kind": "section", "label": here})
            section = here
        rows.append(leaf("flow_steps", "step:" + idname, title, why, glyph=glyph, operator="node.add_node",
                         properties=(("type", idname), ("use_transform", True))))
    return rows


def draw_step_glyph(view, glyph, tx, ty):
    """A step type's 32 px tile, in the + menu's style (teal on soft teal; strokes and shapes only). False for a
    glyph that is not a step's (the + menu draws those)."""
    if glyph not in {"step_image", "step_capture", "step_text", "step_model", "step_place"}:
        return False
    from . import ui_tokens
    teal = ui_tokens.TEAL
    view.shape(tx, ty, 32, 32, ui_tokens.TEAL_SOFT, 8)

    def line(ax, ay, bx, by, width=1.6):
        view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, width)

    if glyph == "step_image":   # A picture: frame, sun, hill.
        for a, b in (((8, 9), (24, 9)), ((24, 9), (24, 23)), ((24, 23), (8, 23)), ((8, 23), (8, 9))):
            line(*a, *b)
        line(9, 10, 15, 17)
        line(15, 17, 18, 14)
        line(18, 14, 23, 10)
        view.shape(tx + 18, ty + 17, 3.5, 3.5, teal, 1.75)
    elif glyph == "step_capture":   # A camera: body, lens, viewfinder.
        view.shape(tx + 7, ty + 10, 18, 12, teal, 3)
        view.shape(tx + 12, ty + 12, 8, 8, ui_tokens.INK, 4)
        view.shape(tx + 14, ty + 14, 4, 4, teal, 2)
        view.shape(tx + 11, ty + 21, 6, 3, teal, 1)
    elif glyph == "step_text":   # Lines of text.
        for y, right in ((22, 24), (17, 24), (12, 18)):
            line(8, y, right, y, 1.8)
    elif glyph == "step_model":   # A cube.
        top = ((16, 25), (24, 21), (16, 17), (8, 21))
        for a, b in zip(top, top[1:] + top[:1]):
            line(*a, *b, 1.4)
        for a, b in (((8, 21), (8, 12)), ((24, 21), (24, 12)), ((16, 17), (16, 8)), ((8, 12), (16, 8)),
                     ((24, 12), (16, 8))):
            line(*a, *b, 1.4)
    elif glyph == "step_place":   # Down into a tray.
        line(16, 25, 16, 13, 1.7)
        line(12, 17, 16, 13, 1.7)
        line(20, 17, 16, 13, 1.7)
        line(8, 12, 8, 8)
        line(8, 8, 24, 8)
        line(24, 8, 24, 12)
    return True


def is_flow_window(area, window=None):
    """A flow editor shown in Angelo's look: the window flow_open opened, or Manual's Generation Flows tab."""
    from . import flow_open, studio_workspaces
    if area is None or area.type != 'NODE_EDITOR':
        return False
    for w in live_windows.windows(bpy.context.window_manager) if window is None else (window,):
        areas = live_windows.areas(w)
        if not areas or areas[0].as_pointer() != area.as_pointer():
            continue
        if flow_open.marked(w) or studio_workspaces.tab_kind(w.workspace) == "flows":
            return True
    return False


def prepare_area(window, area):
    """Hide the stock chrome in ``area``'s flow editor and make sure the bar draws."""
    global _handle
    space = area.spaces.active
    from . import chrome_guard
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        if hasattr(area, "angelo_owned"):   # Angelo's own surface: no ">", "<" or "v" tabs for the hidden regions.
            area.angelo_owned = True        # First, so hiding the regions below rebuilds the zones without them.
    chrome_guard.turn_off(window, area, chrome_guard.shown(area))   # At once, never slid out (chrome_guard.turn_off).
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        overlay = getattr(space, "overlay", None)
        if overlay is not None and hasattr(overlay, "show_context_path"):
            overlay.show_context_path = False   # The flow's name is on the bar; the path would sit under it.
    chrome_guard.guard(area, window)   # And kept hidden by events: N, T or a script never bring the chrome back.
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    area.tag_redraw()


def prepare(window):
    prepare_area(window, live_windows.areas(window)[0])


def bar_height(scale):
    return HEIGHT * scale


def hint(tree, text):
    """Say ``text`` under ``tree``'s name in its bar until a step is added or removed (Turn into a flow's "add
    steps after it")."""
    hints[tree.name] = (text, len(tree.nodes))


BLOCKED_STEP = n_("{step}: {gate}")   # A step that can't run yet, with its node's own gate text.


def blocking(tree):
    """``(step name, its gate text)`` when no generator step of ``tree`` can run (every one is blocked), else None:
    the bar names the first one and Run all is drawn disabled until one can (review P4, P5)."""
    from . import flow_host, flow_nodes
    generators = [n for n in tree.nodes if flow_nodes.TYPE_BY_IDNAME.get(n.bl_idname) in GENERATORS]
    states = [(n, flow_host.node_state(tree, n.name) or {}) for n in generators]
    if not states or any(state.get("state") != "BLOCKED" for _, state in states):
        return None
    node, state = states[0]
    setup = flow_nodes.setup_need(tree, node.name) if hasattr(flow_nodes, "setup_need") else None
    gate = (flow_nodes.SETUP_LABELS.get(setup) if setup and hasattr(flow_nodes, "SETUP_LABELS") else None) or (
        getattr(flow_nodes, "SETUP_TEXT", {}).get(state.get("setup")))
    from .flow_setup import step_label   # The bar and Run all's tooltip quote it: as shown, never as stored.
    return step_label(node.name), iface_(gate) if gate else (state.get("reason") or "")


def status(tree):
    """The line under the flow's name and whether it is a problem: the flow's error, its live runs, a step that
    stops every run, or its size (what the hidden sidebar showed). The start page says what Generation Flows are."""
    if tree is None:
        from . import flow_open
        return iface_(flow_open.MENU_WHY), False
    from . import flow_host
    entry = flow_host._cache.get(tree.name) or {}
    if entry.get("error"):
        return str(entry["error"]), True
    runs = flow_host.active_runs(tree.name)
    if runs:
        started = sum(len(step["assets"]) for run in runs for step in run.steps.values())
        return plural_(started, "Running · {count} generation started", "Running · {count} generations started"), False
    stop = blocking(tree)
    if stop:
        return iface_(BLOCKED_STEP, step=stop[0], gate=stop[1]), True
    said = hints.get(tree.name)
    if said and said[1] == len(tree.nodes):
        return iface_(said[0]), False
    hints.pop(tree.name, None)
    from . import flow_open
    return flow_open.size_text(tree), False


def layout(width, height, scale, measure, title, running=False, link=True):
    """The bar's rectangles in region pixels (y up): the bar, the title's and the status line's baselines, the buttons
    (right-aligned: Stop while a run is live, Add step, Run all) and, at the left, the Media Studio link (``link``)."""
    s = scale
    bar = (0, height - HEIGHT * s, width, HEIGHT * s)
    y = bar[1] + (HEIGHT - BUTTON_H) / 2 * s
    specs = ([("stop", iface_(STOP), "ghost")] if running else []) + [
        ("add", iface_(ADD), "secondary"), ("run", iface_(RUN), "primary")]
    buttons, right = [], width - PAD * s
    for action, label, style in reversed(specs):
        w = measure(label, SIZE * s) + 36 * s
        right -= w
        buttons.insert(0, ((right, y, w, BUTTON_H * s), action, label, style))
        right -= 8 * s
    pill, text_x = None, PAD * s
    if link:   # The way back, on the left (rev 3): the shared pill, icon, 8 px, label, 12 px each side.
        label = iface_(STUDIO)
        w = (2 * PILL_PAD + PILL_ICON + PILL_GAP) * s + measure(label, 12 * s)
        if PAD * s + w + 14 * s + MIN_TITLE * s > buttons[0][0][0]:   # Short of room: its label drops first (section 4).
            label, w = "", (2 * PILL_PAD + PILL_ICON) * s
        pill = ((PAD * s, bar[1] + (HEIGHT - PILL_H) / 2 * s, w, PILL_H * s), "studio", label)
        text_x = PAD * s + w + 14 * s
    return {"bar": bar, "title": (text_x, bar[1] + (HEIGHT / 2 + 2) * s), "status": (text_x, bar[1] + 10 * s),
            "title_width": max(0, buttons[0][0][0] - PAD * s - text_x), "buttons": buttons, "link": pill,
            "title_text": title}


def _draw():
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if area is None or region is None or window is None or not is_flow_window(area, window):
        return
    from . import chat_view, flow_host
    tree = area.spaces.active.node_tree
    state = _bars.setdefault(area.as_pointer(), {"hits": [], "hover": None, "pressed": None})
    line, problem = status(tree)
    running = bool(tree is not None and flow_host.active_runs(tree.name))
    paint(state, region, chat_view.ui_scale(), tree, line, problem, running)
    if window.as_pointer() not in _inputs:
        _start_input(window)


def flow_label(name):
    from .flow_templates import flow_label as label
    return label(name)


def paint(state, region, s, tree, line="", problem=False, running=False):
    """Draw the bar (and the start page with no flow open) into the current framebuffer for ``region`` (anything
    with ``width`` and ``height``) and record its hits in ``state``. Reads no Blender context, so a check can draw it
    offscreen."""
    import gpu
    from . import canvas_rtl, card_draw, card_kit as kit, chat_view
    saved = chat_view._scale
    chat_view._scale = 1.0   # Region pixels: sizes below are already scaled.
    canvas_rtl.begin(region.width)   # Mirrored in a right-to-left language, as the chat and the cards; hits are not.
    try:
        hits = []
        if tree is None:
            from . import flow_start
            hits += flow_start.draw(region, s, state)
        plan = layout(region.width, region.height, s, lambda text, size: chat_view.measure(text, size, scale=1.0),
                      flow_label(tree.name) if tree else iface_("Generation Flows"), running)
        x, y, w, h = plan["bar"]
        chat_view.shape(x, y, w, h, kit.CARD)
        chat_view.shape(x, y, w, max(1, s), kit.LINE)
        title = chat_view.elide(plan["title_text"], plan["title_width"], TITLE * s)
        chat_view.text(title, plan["title"][0], plan["title"][1] if line else
                       y + (HEIGHT - TITLE * 0.72) / 2 * s, TITLE * s, kit.TEXT)
        line = chat_view.elide(line, plan["title_width"], STATUS * s)
        if line:
            chat_view.text(line, plan["status"][0], plan["status"][1], STATUS * s, kit.AMBER if problem else kit.MUTED)
        buttons = plan["buttons"] if tree is not None else []   # Add step and Run all need a flow: the rows start one.
        stop = blocking(tree) if tree is not None else None
        for rect, action, label, style in buttons:
            enabled = not (action == "run" and stop)   # Teal only once a step can run; until then it says why.
            card_draw._button(chat_view_view(), rect, label, style if enabled else "secondary",
                              (state["hover"] == (action, None), state["pressed"] == action, False, enabled), SIZE * s)
            hits.append((rect, action if enabled else "run_blocked", None))
            if not enabled and state["hover"] == ("run_blocked", None):
                from . import canvas_controls
                canvas_controls.tip(chat_view_view(), rect, iface_(BLOCKED_STEP, step=stop[0], gate=stop[1]), s,
                                    region.width, region.height)
        if plan["link"]:
            rect, action, label = plan["link"]
            _pill(rect, label, s, state["hover"] == (action, None), state["pressed"] == action)
            hits.append((rect, action, None))
        state["hits"] = hits
        state["drawn"] = {"title": title, "status": line, "labels": [b[2] for b in buttons], "text_x": plan["title"][0],
                          "rects": {b[1]: list(b[0]) for b in buttons}, "link": plan["link"] and list(plan["link"][0]),
                          "start_rows": [value for _, action, value in hits if action == "row"]}
    finally:
        canvas_rtl.end()
        chat_view._scale = saved
        gpu.state.blend_set('NONE')


def _pill(rect, label, s, hover, pressed):
    """The shared pill (studio_entry_points.rst, section 4): PANEL with a 1 px LINE border, PANEL_HOVER on hover,
    TEAL_SOFT pressed, a 16 px Media Studio glyph (frame and four-point spark) and the label at 12 px."""
    from . import card_kit as kit, chat_view, ui_tokens as tk
    x, y, w, h = rect
    chat_view.shape(x, y, w, h, kit.LINE, h / 2)
    chat_view.shape(x + s, y + s, w - 2 * s, h - 2 * s, tk.TEAL_SOFT if pressed else kit.PANEL_HOVER if hover
                    else kit.PANEL, h / 2 - s)
    from . import canvas_rtl
    ix, iy, unit = x + PILL_PAD * s, y + (h - PILL_ICON * s) / 2, PILL_ICON * s / 16
    with canvas_rtl.upright(ix, PILL_ICON * s) as gx:   # Media Studio's glyph never mirrors (review P7).
        for a, b in (((1, 2), (12, 2)), ((12, 2), (12, 12)), ((12, 12), (1, 12)), ((1, 12), (1, 2))):
            chat_view.stroke(gx + a[0] * unit, iy + a[1] * unit, gx + b[0] * unit, iy + b[1] * unit, kit.TEXT, 1.3 * s)
        for dx, dy in ((0, 3.5), (3.5, 0), (0, -3.5), (-3.5, 0)):
            chat_view.stroke(gx + 12 * unit, iy + 12 * unit, gx + (12 + dx) * unit, iy + (12 + dy) * unit, kit.TEXT,
                             1.4 * s)
    chat_view.text(label, ix + (PILL_ICON + PILL_GAP) * s, y + (h - 12 * s * 0.72) / 2, 12 * s, kit.TEXT)



_view = None


def chat_view_view():
    """``card_draw``'s view: the chat canvas's primitives (``card_host._View``)."""
    global _view
    if _view is None:
        from .card_host import _View
        _view = _View()
    return _view


def _start_input(window):
    """A draw callback cannot start an operator: the window's input handler starts on the next tick."""
    pointer = window.as_pointer()
    _inputs.add(pointer)

    def start(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        if target is None:
            _inputs.discard(pointer)
            return None
        with bpy.context.temp_override(window=target, screen=target.screen):
            bpy.ops.angelo.flow_window_input('INVOKE_DEFAULT')
        return None
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, start), first_interval=0.01)


def _target(window, event):
    """The flow area, its WINDOW region and the hit (action, value) under the pointer, if any."""
    for area in live_windows.areas(window):
        state = _bars.get(area.as_pointer())
        if state is None or not is_flow_window(area, window):
            continue
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            continue
        from . import canvas_rtl   # Hits are laid out left to right; the pointer is mirrored to match the drawing.
        x, y = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), event.mouse_y - region.y
        if not (0 <= x < region.width and 0 <= y < region.height):
            continue
        for (rx, ry, rw, rh), action, value in state["hits"]:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return area, region, (action, value)
        return area, region, None
    return None, None, None


def in_bar(region, event):
    from . import chat_view
    y = event.mouse_y - region.y
    return 0 <= event.mouse_x - region.x < region.width and region.height - bar_height(chat_view.ui_scale()) <= y < region.height


def act(window, area, region, hit):
    action, value = hit
    tree = area.spaces.active.node_tree
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        if action == "add":   # Angelo's dropdown of the step types, from the button's left edge, under the bar.
            from . import flow_menu
            rect = next((r for r, a, _ in _bars.get(area.as_pointer(), {}).get("hits", []) if a == "add"), (0, 0, 0, 0))
            from .ui_text import is_rtl   # The button's left edge where it shows (the bar mirrors right to left).
            anchor = region.x + (region.width - rect[0] - rect[2] if is_rtl() else rect[0])
            bpy.ops.angelo.flow_menu('INVOKE_DEFAULT', menu=flow_menu.STEPS, anchor=anchor, top=HEIGHT)
        elif action == "run" and tree is not None:
            bpy.ops.angelo.flow_run('INVOKE_DEFAULT', tree=tree.name, scope='ALL')
        elif action == "stop" and tree is not None:
            bpy.ops.angelo.flow_stop(tree=tree.name)
        elif action == "studio":   # The way back: the shared opener when registered (Director: its window).
            if hasattr(bpy.types, "ANGELO_OT_open_media_studio"):
                bpy.ops.angelo.open_media_studio(kind='AUTO')
            else:
                bpy.ops.angelo.toggle_studio(mode='OPEN')
        elif action == "row":
            row = _bars.get(area.as_pointer(), {}).get("rows", {}).get(value)
            if row and row.get("operator"):
                group, name = row["operator"].split(".", 1)
                getattr(getattr(bpy.ops, group), name)('INVOKE_DEFAULT', True, **dict(row.get("properties") or ()))


class ANGELO_OT_flow_window_input(Operator):
    """Clicks on a flow window's bar and start page (internal)."""
    bl_idname = "angelo.flow_window_input"
    bl_label = "Flow Window Bar"
    bl_options = {'INTERNAL'} | modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        self.window, self.pressed = context.window.as_pointer(), None
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from . import input_policy
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}   # Timers, window events and the quit key, wherever the pointer is.
        window = live_windows.find(context.window_manager, self.window, live=False)   # Between screens: no hit, kept.
        if window is None:
            _inputs.discard(self.window)
            return {'FINISHED', 'PASS_THROUGH'}
        if (event.type not in {'MOUSEMOVE', 'LEFTMOUSE'} or context.window is None
                or context.window.as_pointer() != self.window):
            return {'PASS_THROUGH'}
        area, region, hit = _target(window, event)
        if area is None:
            return {'PASS_THROUGH'}
        state = _bars[area.as_pointer()]
        if event.type == 'MOUSEMOVE':   # The hovered (action, value): a bar button's value is None.
            if hit != state["hover"]:
                state["hover"] = hit
                area.tag_redraw()
            return {'PASS_THROUGH'}
        if event.value == 'PRESS' and (hit or in_bar(region, event)):
            self.pressed = (area.as_pointer(), hit)
            state["pressed"] = hit[0] if hit else None
            area.tag_redraw()
            return {'RUNNING_MODAL'}   # The bar and the rows are not the canvas: no box select starts under them.
        if event.value == 'RELEASE' and self.pressed is not None:
            pressed, self.pressed = self.pressed, None
            state["pressed"] = None
            area.tag_redraw()
            if hit and pressed == (area.as_pointer(), hit):
                act(window, area, region, hit)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


CLASSES = (ANGELO_OT_flow_window_input,)


def owned_areas(windows, workspaces):
    """The editors Angelo shows full-area in a loaded file: each flow window's editor (its screen carries
    ``flow_open.MARK``) and the only editor of each Generation Flows and Media Studio workspace tab."""
    from . import flow_open, studio_workspaces
    areas = [live_windows.areas(w)[0] for w in windows if live_windows.areas(w) and flow_open.marked(w)]
    for workspace in workspaces:
        if studio_workspaces.tab_kind(workspace) in {"flows", "studio"}:
            areas += [live_windows.screen_areas(s)[0] for s in workspace.screens if live_windows.screen_areas(s)]
    return areas


def _load_post(*_args):
    """Angelo-owned is runtime state (``Area.angelo_owned``, reset on every file read): mark Angelo's editors again,
    so a reopened file shows no region-toggle arrows there either."""
    for area in owned_areas(live_windows.windows(bpy.context.window_manager), bpy.data.workspaces):
        if hasattr(area, "angelo_owned") and not area.angelo_owned:
            area.angelo_owned = True


def register():
    global _handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if _handle is None:   # Always: a saved Generation Flows tab draws its bar after a reload too.
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.handlers.load_post.append(bpy.app.handlers.persistent(_load_post))   # Kept across file loads.


def unregister():
    global _handle
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _handle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    _bars.clear()
    _inputs.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
