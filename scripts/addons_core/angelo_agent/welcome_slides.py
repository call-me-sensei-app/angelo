# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The welcome card's quick tour of features: a slideshow under *What you can do* (1.0.5).

Slides go in the order a new person benefits from them: the chat first (the one path, welcome ▸ Start creating ▸
connect ▸ describe it), then Media Studio (start), Generation Flows (go further) and the generated assets they
land in. Copy, pills and actions are doc/angelo/studio_entry_points.rst's ("First run"). Each slide has a vector
picture (``welcome_art``), a headline, one line and a secondary pill whose verb says where it goes; it closes the
welcome, then opens the feature through the opener every other entry uses (``open_feature``). *Start creating*
stays the card's one primary button. The assets slide shows only where the Asset Shelf can be opened on the
Angelo Media Studio tab (``SHELF_OPENER``), so it never promises a feature this build lacks.

It advances by itself every ``DWELL`` seconds (the maintainer's "quick slide show"; ``auto_next``): it holds while
the pointer is over it or a control in it has keyboard focus, stops for good once the person navigates, comes to
rest on the last slide, and never moves under Reduced Motion (Preferences ▸ Interface), which also drops the fade.
Arrows, dots, the Left and Right keys and a sideways trackpad swipe change slides (mirrored in a right-to-left
language). Every slide is laid out at the height of the tallest, in this language, so changing slides never moves
anything else; where even the welcome's smallest scale would not fit, the slideshow goes ``compact``. This module
lays out and draws; ``intro.py`` owns the clock, input and closing.
"""

import importlib

from . import canvas_rtl, card_kit as kit, live_windows, ui_text, welcome_art
from .ui_text import iface_, n_

# key, headline, line, pill, tooltip.
ALL_SLIDES = (
    ("chat", n_("Describe it, Angelo builds it"),
     n_("Ask in plain words and watch Angelo build it in your scene."),
     n_("Try an example"), n_("Show the chat with an example request, ready to send")),
    ("studio", n_("Start in Media Studio"), n_("Generate images and 3D models in one step."),
     n_("Open Media Studio"), n_("Media Studio: generate images and 3D models in one step")),
    ("flows", n_("Go further with Generation Flows"), n_("Chain generation steps into a flow you can rerun."),
     n_("Open Generation Flows"), n_("Generation Flows: go further, chain generation steps into a flow you can rerun")),
    ("assets", n_("Everything you make, ready to use"),
     n_("Your images and models appear as assets in the Asset Browser and Asset Shelf."),
     n_("Show my assets"), n_("Show the Asset Shelf on its Angelo Media Studio tab")),
)
# Director shows no shelf, so there the assets pill switches to Manual first, and says so (the spec: a label always
# says what the click does). Chosen when the slide draws, so it never goes stale after a mode switch.
DIRECTOR_ASSETS = (n_("Show in Manual"), n_("Switch to Manual and show the Asset Shelf on its Angelo Media Studio tab"))
# The Asset Shelf's Angelo Media Studio tab: (module, function taking the window), owned by the shelf (part D).
SHELF_OPENER = ("media_shelf", "show_studio_tab")
HEADING = n_("What you can do")
DWELL = 6.5    # Seconds a slide stays before the next one.
FADE = 0.25    # Seconds a new slide takes to fade in.
SWIPE = 60     # Pixels of sideways trackpad travel that change a slide.

PAD, GAP = 14, 22


def _shelf_opener():
    try:
        module = importlib.import_module("." + SHELF_OPENER[0], __package__)
    except ImportError:
        return None
    return getattr(module, SHELF_OPENER[1], None)


def slides():
    """The slides this build can keep the promise of: the assets slide needs the shelf's Angelo Media Studio tab."""
    return [slide for slide in ALL_SLIDES if slide[0] != "assets" or _shelf_opener() is not None]


def keys():
    return [slide[0] for slide in slides()]


def step(index, delta):
    return (index + delta) % len(slides())


def inside(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


def key_step(event_type):
    """The slide change for an arrow key: Left goes to the slide on the left, which is the next one in a
    right-to-left language (the dots and arrows are mirrored there)."""
    delta = {'LEFT_ARROW': -1, 'RIGHT_ARROW': 1}.get(event_type)
    if delta is None:
        return None
    return -delta if ui_text.is_rtl() else delta


def held(over_slides, focus):
    """The slideshow stays on its slide while the pointer is over it or a control in it has keyboard focus."""
    return bool(over_slides) or bool(focus and str(focus[0]).startswith("slide_"))


def auto_next(index, count, waited, held_, reduce_motion, stopped):
    """The slide to advance to by itself, or None (doc/angelo/studio_entry_points.rst, "First run"): after ``DWELL``
    seconds, never while held, under Reduced Motion or once the person has navigated, and never past the last slide,
    where it comes to rest (a modal card must not keep spinning while the person looks away)."""
    if stopped or reduce_motion or held_ or waited < DWELL or index >= count - 1:
        return None
    return index + 1


def swipe_step(travel, dx, dy):
    """(slide change or None, travel left over) for a trackpad pan of (dx, dy) pixels: sideways travel past ``SWIPE``
    is one slide, content following the fingers as in every carousel (fingers left: the next slide); a mostly
    vertical pan is not a swipe."""
    if abs(dy) > abs(dx):
        return None, 0.0
    travel += dx
    if abs(travel) < SWIPE:
        return None, travel
    delta = -1 if travel > 0 else 1
    return (-delta if ui_text.is_rtl() else delta), 0.0


def pill(key, here="director"):
    """A slide's pill label and tooltip in the mode the window is in (``here``: "director" or "standard")."""
    if key == "assets" and here == "director":
        return tuple(iface_(text) for text in DIRECTOR_ASSETS)
    return next(((iface_(label), iface_(tip)) for k, _, _, label, tip in ALL_SLIDES if k == key), ("", ""))


def tooltip(key, here="director"):
    return pill(key, here)[1]


def _measure(view, text, size):
    from .ui_kit import measure
    return measure(view, text, size)


def layout(view, wrap, x, top, width, s, index, hover=None, alpha=1.0, compact=False, pressed=None,
           here="director"):
    """The slideshow's box under its heading, at ``x``, its heading's baseline ``top``: returns (ops, hits,
    rect, height). ``wrap(view, text, width, size)`` is the welcome's wrapper, so lines match its measurement.

    ``compact`` (a 3D view too short for the whole welcome, or a narrow one): no picture, and Try it with the arrows
    on the headline's row, so the welcome still fits where its smallest scale would not."""
    ops, hits = [], []
    shown = slides()
    index %= len(shown)
    ops.append(("text", iface_(HEADING), x, top, 12.5 * s, kit.MUTED))
    box_top = top - 14 * s
    pad = PAD * s
    compact = compact or width < 520 * s
    pic_w = 0 if compact else min(204 * s, width * 0.32)   # Small: the welcome must fit a laptop.
    pic_h = pic_w * 0.58
    text_x = x + pad + (4 * s if compact else pic_w + GAP * s)
    right = x + width - 20 * s
    label = pill(shown[index][0], here)[0]
    button_w = max(_measure(view, pill(slide[0], here)[0], 12 * s) for slide in shown) + 24 * s   # Nothing moves.
    controls_h, arrow, dot, active, spacing = 28 * s, 26 * s, 7 * s, 20 * s, 8 * s
    dots_w = (len(shown) - 1) * (dot + spacing) + active
    nav_w = 2 * arrow + 20 * s + dots_w
    title_w = right - text_x - (button_w + 16 * s + nav_w + 16 * s if compact else 0)
    texts = [(wrap(view, iface_(title), title_w, 17 * s), wrap(view, iface_(line), right - text_x, 13.5 * s))
             for _, title, line, _, _ in shown]
    if compact:
        height = max(max(len(t) * 22 * s, controls_h) + 6 * s + len(b) * 19 * s for t, b in texts) + 2 * pad + 4 * s
    else:
        height = max(pic_h + 2 * pad, max(18 * s + len(t) * 22 * s + 6 * s + len(b) * 19 * s for t, b in texts)
                     + 14 * s + controls_h + pad)
    rect = (x, box_top - height, width, height)
    ops.append(("shape", rect, kit.PANEL, 12 * s))
    key = shown[index][0]
    if not compact:
        ops.append(("slide_picture", key, (x + pad, box_top - pad - pic_h, pic_w, pic_h), alpha))
    title, lines = texts[index]
    fade = lambda color: (color[0], color[1], color[2], color[3] * alpha)   # noqa: E731
    if compact:   # The headline centred on the controls' row; the line under the taller of the two.
        controls_y = box_top - pad - controls_h
        ty = controls_y + controls_h / 2 - 0.36 * 17 * s + (len(title) - 1) * 11 * s
        body_top = box_top - pad - max(len(title) * 22 * s, controls_h) - 6 * s
    else:
        controls_y = rect[1] + pad
        ty = box_top - 18 * s - 13 * s
        body_top = ty - len(title) * 22 * s + 13 * s - 6 * s
    for line in title:
        ops.append(("text", line, text_x, ty, 17 * s, fade(kit.TEXT)))
        ty -= 22 * s
    ty = body_top - 13 * s
    for line in lines:
        ops.append(("text", line, text_x, ty, 13.5 * s, fade(kit.MUTED)))
        ty -= 19 * s
    button_x = right - nav_w - 16 * s - button_w if compact else text_x
    button = (button_x, controls_y, button_w, controls_h)
    state = "pressed" if pressed == ("slide_try", key) else "hover" if hover == ("slide_try", key) else None
    ops.append(("slide_button", button, label, state, alpha))
    if state == "hover":
        ops.append(("slide_tip", button, tooltip(key, here)))
    hits.append((button, "slide_try", key))
    # Arrows and dots at the box's far end, on the button's row: ‹ • • • • ›.
    cy = controls_y + controls_h / 2
    nx = right - arrow
    px = nx - 10 * s - dots_w - 10 * s - arrow
    ops.append(("slide_arrow", (px, cy - arrow / 2, arrow, arrow), -1, hover == ("slide_prev", None)))
    hits.append(((px, cy - arrow / 2, arrow, arrow), "slide_prev", None))
    ops.append(("slide_arrow", (nx, cy - arrow / 2, arrow, arrow), 1, hover == ("slide_next", None)))
    hits.append(((nx, cy - arrow / 2, arrow, arrow), "slide_next", None))
    dx = px + arrow + 10 * s
    for i in range(len(shown)):
        w = active if i == index else dot
        ops.append(("slide_dot", (dx, cy - dot / 2, w, dot), i == index, hover == ("slide_dot", i)))
        if i != index:
            hits.append(((dx - spacing / 2, cy - 13 * s, w + spacing, 26 * s), "slide_dot", i))
        dx += w + spacing
    return ops, hits, rect, height + 14 * s


def draw(view, op, s):
    """Draw one of this module's own ops (``welcome_screen.draw`` hands them over)."""
    kind = op[0]
    if kind == "slide_picture":
        _, key, (x, y, w, h), alpha = op
        with canvas_rtl.upright(x, w) as left:
            welcome_art.draw(view, key, (left, y, w, h), s, alpha)
    elif kind == "slide_button":   # Secondary (the spec's pill): never teal, so Start creating stays the one primary.
        _, (x, y, w, h), label, state, alpha = op
        fill = kit.TEAL_SOFT if state == "pressed" else kit.PANEL_HOVER if state == "hover" else kit.PANEL
        view.shape(x, y, w, h, kit.LINE, h / 2)
        view.shape(x + s, y + s, w - 2 * s, h - 2 * s, fill, h / 2 - s)   # A 1 px LINE border.
        width = _measure(view, label, 12 * s)
        view.text(label, x + (w - width) / 2, y + h / 2 - 0.36 * 12 * s, 12 * s, (*kit.TEXT[:3], alpha))
    elif kind == "slide_tip":
        from . import canvas_controls
        canvas_controls.tip(view, op[1], op[2], s)
    elif kind == "slide_arrow":   # A chevron: mirrored with the layout, so it always points where it goes.
        _, (x, y, w, h), direction, hovered = op
        view.shape(x, y, w, h, kit.LINE_HOVER if hovered else kit.LINE, w / 2)
        cx, cy, d = x + w / 2, y + h / 2, 3.5 * s
        tip, back = cx + direction * d * 0.9, cx - direction * d * 0.6
        view.stroke(back, cy + d, tip, cy, kit.TEXT, 1.8 * s)
        view.stroke(tip, cy, back, cy - d, kit.TEXT, 1.8 * s)
    elif kind == "slide_dot":
        _, (x, y, w, h), current, hovered = op
        view.shape(x, y, w, h, kit.TEAL if current else kit.MUTED if hovered else kit.LINE_HOVER, h / 2)


def open_feature(window, key):
    """Try it: once the welcome's modal handler has ended (a timer), open ``key``'s feature through the opener every
    other entry uses, so it lands in that mode's home (doc/angelo/studio_entry_points.rst, "Behaviour")."""
    import bpy
    pointer = window.as_pointer()

    def run(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        if target is None:
            return None
        try:
            OPENERS[key](target)
        except RuntimeError as error:   # No window could open here: said as the openers say it, never raised.
            print("Angelo welcome:", error)
        return None

    # settle: an opener may open the chat or a window; one between screens would not count and a second would open.
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, run, settle=True),
                            first_interval=0.05)


def _open_chat(window):
    """The chat shown as its top-bar button shows it, an example request in its composer, unsent, and the composer
    focused (the empty chat's own example mechanism, ``angelo.chat_example``). Before a connection the chat shows
    its connect card and its waiting field takes no text, so nothing is put there."""
    import bpy
    from . import chat_toggle, chat_view, console, ui
    if chat_toggle.chat_state(window, live_windows.every_window(bpy.context.window_manager))[0] == "hidden":
        chat_toggle.show(window)
    pointer, tries = window.as_pointer(), [40]

    def fill(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        area = next((a for a in live_windows.areas(target) if console.is_chat(a)), None)
        view = chat_view._views.get(area.as_pointer()) if area else None
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if view is None or region is None:
            tries[0] -= 1
            return 0.05 if tries[0] > 0 else None   # The chat appears on a later tick.
        if ui.connection_available():
            with bpy.context.temp_override(window=target, screen=target.screen, area=area, region=region):
                bpy.ops.angelo.chat_example(index=0)
            view["focus_request"] = True
        area.tag_redraw()
        return None
    step = live_windows.deferred(lambda: bpy.context.window_manager, pointer, fill)
    if step() is not None:
        bpy.app.timers.register(step, first_interval=0.05)


def _open_studio(window):
    import bpy
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.open_media_studio(kind='AUTO')   # Manual: its tab; Director: its window.


def _open_flows(window):
    """Manual: the Generation Flows tab (``angelo.show_flows``). Director: the Flows popup under the top bar's Flows
    button (part A's ``flow_menu.open_from_top_bar``), never at the pointer, which after the welcome is anywhere."""
    import bpy
    from . import studio_workspaces
    try:
        from . import flow_menu
    except ImportError:
        flow_menu = None
    anchored = getattr(flow_menu, "open_from_top_bar", None)
    if anchored is not None and not studio_workspaces.in_manual(window):
        anchored(window)
        return
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.show_flows()


def _open_assets(window):
    """The 3D view's Asset Shelf on its Angelo Media Studio library (``media_shelf.show_studio_tab``, part D): the one
    pill that changes a layout, and its slide names it. Director shows no shelf, so there it first switches to
    Manual, as Try Manual mode does; the shelf is shown once Manual's layout has settled. The shelf returns False
    while it cannot show (no 3D view yet, not Object mode, the library not registered yet); it is retried briefly,
    then left as it is, never forced."""
    import bpy
    from . import director
    opener = _shelf_opener()
    if opener is None:
        return
    if director.is_director(window):
        director.request_mode(window, "MANUAL")
    pointer, tries = window.as_pointer(), [40]

    def show(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        if target is None or director.is_director(target) or not opener(target):
            tries[0] -= 1
            return 0.1 if target is not None and tries[0] > 0 else None
        return None
    step = live_windows.deferred(lambda: bpy.context.window_manager, pointer, show, settle=True)   # Opens a window.
    if step() is not None:
        bpy.app.timers.register(step, first_interval=0.1)


OPENERS = {"chat": _open_chat, "studio": _open_studio, "flows": _open_flows, "assets": _open_assets}
