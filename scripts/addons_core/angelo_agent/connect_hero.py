# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A showcase over the 3D view while the chat has no connection.

Until a connection is ready the chat's connect options are the only call to
action (the composer is hidden, ``chat_view``), and the 3D view, which would
show Blender's empty default scene, shows this instead: a render from
``MEDIA``, its caption, a line pointing to the chat and a quiet "Explore the
empty scene" link. When a connection becomes ready it fades out (0.35 s; at
once with Reduce Motion), revealing the scene ready to create in, and then
releases everything it holds: draw handler, input handler, timer and texture.
While it is shown nothing animates and nothing redraws on its own.

With the first-launch welcome screen (``intro.py``) and Angelo's cards
(``card_host``: the setup card, the start panel, notices): each covers the same
3D view. While one is open it paints the showcase beneath itself
(``paint_beneath``), and the showcase's own handler neither draws nor takes the
input the welcome or card takes, whichever opened first. So a first launch
reads the splash's art, then the setup card and the welcome over the showcase,
then the showcase beside the connect card, then the scene once connected. The
empty scene never shows before a connection unless the person chooses it.

Input: only over its own 3D view. Pointer and plain keys stop there, so nothing
edits the hidden scene; timers, window events, Blender's motionless hover refresh
(``input_policy.refresh_move``, which a quit waits for) and Ctrl/Cmd or F-key shortcuts
pass through, and so do Tab, Return, Space and Esc while the chat has keyboard
focus, which move through and choose its connect options. The chat counts it as a canvas, not a foreground tool
(``input_policy.CANVAS_INPUTS``), so the connect options stay usable beside it.

Media: ``MEDIA`` is the one table. An ``image`` entry ships in ``assets/`` (a
few hundred KB at most). A ``video`` entry (``url``, ``poster``, ``bytes``) is
the slot for the recorded bridge build; today the slot shows its poster still.
The player that will replace it must fetch a video too large to bundle on first
show, and play it only while the showcase is visible and its window active.
``ANGELO_DISABLE_HERO=1`` turns the showcase off for automation.
"""

import time
import traceback
from array import array
from pathlib import Path

import bpy
from bpy.types import Operator

from . import canvas_controls, canvas_rtl, input_policy, product_env
from .ui_text import iface_, n_

ASSETS = Path(__file__).resolve().parent / "assets"
MEDIA = ({"kind": "image", "file": "hero-stone-bridge.webp", "title": n_("A stone bridge at dusk")},)
EYEBROW = n_("Made with Angelo")
PROMPT = n_("Connect an AI account in the chat to start creating.")
EXPLORE = n_("Explore the empty scene")
FADE_SECONDS = 0.35
BACKDROP = (0.090, 0.098, 0.118, 1)  # welcome_screen.CARD
TEXT, MUTED, MINT = (0.953, 0.925, 0.886, 1), (0.74, 0.70, 0.64, 1), (0.631, 0.957, 0.839, 1)
_state = {}  # window, area, pixels, size, texture, fading_since, hover, pressed, focus, plan, token
_dismissed = False
_handler = _timer = _shader = None
shown = 0  # Times the showcase appeared this session; window fixtures read it.
last_error = None  # A drawing failure dismisses the showcase rather than leaving the 3D view covered.
CHAT_FOCUS_KEYS = frozenset({"TAB", "RET", "NUMPAD_ENTER", "SPACE", "ESC"})  # canvas_controls.key
WINDOW_EVENTS = frozenset({"TIMER", "TIMER0", "TIMER1", "TIMER2", "TIMER_JOBS", "TIMER_AUTOSAVE", "TIMER_REPORT",
                           "TIMERREGION", "WINDOW_DEACTIVATE", "NONE"})


def awaiting_connection(ui):
    """An empty chat with no usable connection: the state the showcase and the hidden composer share."""
    controller = ui.get_controller()
    return not controller.state["messages"] and not ui.connection_available()


def still(entry):
    """The file the slot shows now: an image entry's file, a video entry's poster."""
    return ASSETS / (entry["file"] if entry["kind"] == "image" else entry["poster"])


def is_shown():
    return bool(_state)


def _load():
    image = None
    try:
        image = bpy.data.images.load(str(still(MEDIA[0])), check_existing=False)
        image.colorspace_settings.name = 'Non-Color'  # Drawn as stored, like the welcome banner.
        width, height = image.size
        if not width or not height:
            raise ValueError("The showcase still did not decode")
        pixels = array('f', [0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        return pixels, (width, height)
    except (OSError, RuntimeError, ValueError):
        return None, None  # A missing asset leaves the backdrop and text, never an error.
    finally:
        if image is not None:
            bpy.data.images.remove(image)


def _target(window):
    areas = [a for a in window.screen.areas if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height, default=None)


def _alpha():
    started = _state.get("fading_since")
    if started is None:
        return 1.0
    return max(0.0, 1.0 - (time.monotonic() - started) / FADE_SECONDS)


def _show(ui, window, area):
    global _handler, shown
    pixels, size = _load()
    _state.clear()
    _state.update(window=window.as_pointer(), area=area.as_pointer(), pixels=pixels, size=size, texture=None,
                  fading_since=None, hover=None, pressed=None, focus=None, plan=None, token=object())
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.angelo.connect_hero('INVOKE_DEFAULT')
    shown += 1
    area.tag_redraw()


def fade_out():
    """Start the fade (a connection is ready, or the person chose the empty scene)."""
    if _state and _state["fading_since"] is None:
        still_motion = getattr(bpy.context.preferences.view, "use_reduce_motion", False)
        _state["fading_since"] = time.monotonic() - (FADE_SECONDS if still_motion else 0)


def _release():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _state.clear()  # Drops the pixels and the GPU texture; the input handler sees it and finishes.


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.as_pointer() == _state.get("area"):
                area.tag_redraw()


def _tick():
    """Show it while the chat awaits a connection; fade it once one is ready. Stops after it is gone."""
    global _timer
    from . import director, intro, ui
    if product_env.flag("DISABLE_HERO") or not bpy.context.window_manager.windows:
        _timer = None
        return None
    if _state.get("fading_since") is not None:
        _redraw()
        if _alpha() > 0:
            return 1 / 60
        _release()
        _timer = None
        return None
    if director._timer is not None or ui._layout_timer is not None:
        return 0.25  # The layout is still settling; the 3D view may be replaced.
    wanted = awaiting_connection(ui) and not _dismissed
    if not _state:
        if not wanted:
            _timer = None
            return None
        window = bpy.context.window_manager.windows[0]
        area = _target(window)
        if area is not None and not intro.is_open():
            _show(ui, window, area)
        return 0.25
    if not wanted:
        fade_out()
        return 1 / 60
    return 0.25


def watch():
    global _timer
    if _timer is None and not product_env.flag("DISABLE_HERO"):
        _timer = _tick
        bpy.app.timers.register(_tick, first_interval=0.5)


def layout(view, width, height, scale, size):
    """The showcase's rectangles and lines in region pixels, with its one link as a hit."""
    s = scale
    margin = max(32 * s, 0.06 * min(width, height))
    text_block = 96 * s
    aspect = (size[0] / size[1]) if size else 16 / 9
    w = min(width - 2 * margin, (height - 2 * margin - text_block) * aspect, 1280 * s)
    h = w / aspect
    x = (width - w) / 2
    y = (height - h - text_block) / 2 + text_block
    prompt = [line.rstrip() for line, _ in view.wrap(iface_(PROMPT), max(80, int(w)), int(17 * s), view._font)]
    link = iface_(EXPLORE)
    import blf
    blf.size(view._font, int(14 * s))
    link_width = blf.dimensions(view._font, link)[0]
    link_rect = ((width - link_width) / 2 - 6 * s, y - text_block + 10 * s, link_width + 12 * s, 24 * s)
    return {"image": (x, y, w, h), "prompt": prompt, "link": link, "link_rect": link_rect, "scale": s,
            "hits": [(link_rect, "explore", None)]}


def _yields(area_pointer):
    """The welcome or a card is open over the showcase's view: it paints the showcase beneath itself."""
    from . import card_host, intro
    return intro.is_open() or card_host.shown_on(area_pointer)


def _draw():
    area = bpy.context.area
    if area is not None and not _yields(area.as_pointer()):
        paint_beneath(area, bpy.context.region)


def paint_beneath(area, region):
    """Paint the showcase into this 3D view's region, if it is the one the showcase covers."""
    global _dismissed, last_error
    if not _state or area is None or region is None or area.as_pointer() != _state["area"]:
        return
    canvas_rtl.begin(region.width)  # Mirrored in a right-to-left language.
    try:
        _paint(region)
    except Exception:  # Never leave the scene covered by a half-drawn showcase.
        import gpu
        gpu.state.blend_set('NONE')
        last_error = traceback.format_exc()
        print("Angelo: the showcase could not be drawn and was dismissed\n" + last_error, flush=True)
        _dismissed = True
        _state["fading_since"] = time.monotonic() - FADE_SECONDS  # The timer releases it on its next tick.
    finally:
        canvas_rtl.end()


def _paint(region):
    global _shader
    from . import chat_view as view
    import gpu
    from gpu_extras.batch import batch_for_shader
    alpha, s = _alpha(), bpy.context.preferences.system.ui_scale
    plan = layout(view, region.width, region.height, s, _state["size"])
    _state["plan"] = plan
    gpu.state.blend_set('ALPHA')
    view.shape(0, 0, region.width, region.height, BACKDROP[:3] + (alpha,))
    x, y, w, h = plan["image"]
    if _state["pixels"] is not None:
        if _state["texture"] is None:
            width, height = _state["size"]
            _state["texture"] = gpu.types.GPUTexture((width, height), format='RGBA16F', data=gpu.types.Buffer(
                'FLOAT', [width * height * 4], _state["pixels"]))
        if _shader is None:
            _shader = gpu.shader.from_builtin('IMAGE_COLOR')
        with canvas_rtl.upright(x, w) as ix:  # A picture is never mirrored.
            quad = {"pos": [(ix, y), (ix + w, y), (ix + w, y + h), (ix, y + h)], "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}
            batch = batch_for_shader(_shader, 'TRIS', quad, indices=[(0, 1, 2), (0, 2, 3)])
            gpu.state.blend_set('ALPHA')
            _shader.bind()
            _shader.uniform_sampler("image", _state["texture"])
            _shader.uniform_float("color", (1, 1, 1, alpha))
            batch.draw(_shader)
    radius = 16 * s
    for corner in ((x, y, 0), (x + w, y, 1), (x + w, y + h, 2), (x, y + h, 3)):  # Rounded corners on the backdrop.
        _fillet(view, *corner, radius, BACKDROP[:3] + (alpha,))
    view.shape(x, y, w, 110 * s, (0, 0, 0, 0.38 * alpha), radius)  # A caption band, as on a gallery card.
    view.text(iface_(EYEBROW), x + 24 * s, y + 64 * s, 12 * s, MINT[:3] + (alpha,))
    view.text(iface_(MEDIA[0]["title"]), x + 24 * s, y + 30 * s, 22 * s, TEXT[:3] + (alpha,))
    for index, line in enumerate(plan["prompt"]):
        view.text(line, (region.width - _width(view, line, 17 * s)) / 2, y - 34 * s - index * 24 * s, 17 * s,
                  TEXT[:3] + (alpha,))
    lit = _state.get("hover") == ("explore", None)
    rect = plan["link_rect"]
    colour = (canvas_controls.LINK_HOVER if lit else canvas_controls.LINK)[:3] + (alpha,)
    view.text(plan["link"], rect[0] + 6 * s, rect[1] + 7 * s, 14 * s, colour)
    view.stroke(rect[0] + 6 * s, rect[1] + 4 * s, rect[0] + rect[2] - 6 * s, rect[1] + 4 * s, colour, 1)
    if alpha >= 1:  # Focus ring and pressed state, as everywhere else on Angelo's canvases.
        canvas_controls.draw(view, plan["hits"], _state.get("hover"), _state.get("pressed"), _state.get("focus"),
                             styles={("explore", None): "link"}, scale=s, layers=False)
    gpu.state.blend_set('NONE')


def _width(view, text, size):
    import blf
    blf.size(view._font, int(size))
    return blf.dimensions(view._font, text)[0]


def _fillet(view, x, y, quadrant, radius, color):
    """Cover a corner outside a quarter circle, so the image reads as rounded on the backdrop."""
    import math
    import gpu
    from gpu_extras.batch import batch_for_shader
    sx, sy = (1, 1) if quadrant == 0 else (-1, 1) if quadrant == 1 else (-1, -1) if quadrant == 2 else (1, -1)
    cx, cy = x + sx * radius, y + sy * radius
    start = (180, 270, 0, 90)[quadrant]
    arc = [math.radians(start + i * 9) for i in range(11)]
    points = [(x, y, 0)] + [(cx + radius * math.cos(a), cy + radius * math.sin(a), 0) for a in arc]
    fan = [(0, i, i + 1) for i in range(1, len(points) - 1)]
    batch = batch_for_shader(view._shader, 'TRIS', {"pos": points}, indices=fan)
    gpu.state.blend_set('ALPHA')
    view._shader.bind()
    view._shader.uniform_float("color", color)
    batch.draw(view._shader)


def _chat_has_focus():
    from . import chat_view
    return any(view.get("focused") for view in chat_view._views.values())


class ANGELO_OT_connect_hero(Operator):
    """Input for the showcase: its link, and nothing reaching the hidden empty scene behind it"""
    bl_idname = "angelo.connect_hero"
    bl_label = "Showcase"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.token = _state.get("token")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _dismissed
        from . import intro
        if not _state or _state.get("token") is not self.token:
            return {'FINISHED', 'PASS_THROUGH'}  # Closed: whatever this event is, it belongs to what is below.
        if intro.is_open() or _state["fading_since"] is not None or context.window.as_pointer() != _state["window"]:
            return {'PASS_THROUGH'}
        function_key = event.type[:1] == "F" and event.type[1:].isdigit()
        if event.type in WINDOW_EVENTS or event.ctrl or event.oskey or function_key:
            return {'PASS_THROUGH'}  # Timers, window events and app shortcuts (save, open, preferences) still work.
        area = next((a for a in context.window.screen.areas if a.as_pointer() == _state["area"]), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if region is None or "plan" not in _state or _state["plan"] is None:
            return {'PASS_THROUGH'}
        if event.type in CHAT_FOCUS_KEYS and _chat_has_focus():
            return {'PASS_THROUGH'}  # Keyboard focus is on the chat's connect options: Tab and Return are theirs.
        if not (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height):
            return {'PASS_THROUGH'}  # The chat and everything else stay usable.
        x = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)
        from . import card_host
        if card_host.takes_input(_state["area"], x, event.mouse_y - region.y):
            return {'PASS_THROUGH'}  # A card over the showcase (opened before or after it) takes its own input.
        hit = canvas_controls.hit_at(_state["plan"]["hits"], x, event.mouse_y - region.y)
        key = (hit[1], hit[2]) if hit else None
        if event.type == 'MOUSEMOVE':
            context.window.cursor_set('HAND_POINT' if key else 'DEFAULT')
            if key != _state["hover"]:
                _state["hover"] = key
                area.tag_redraw()
            if input_policy.refresh_move(event):
                return {'PASS_THROUGH'}  # Blender's own refresh: a quit requested before the showcase waits for it.
        elif event.type == 'TAB' and event.value == 'PRESS':
            _state["focus"] = None if _state["focus"] else ("explore", None)
            area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            _state["pressed"], _state["focus"] = key, None
            area.tag_redraw()
        elif (event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and key and _state["pressed"] == key) or (
                event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS' and _state["focus"]):
            _dismissed = True  # The person chose the empty scene: it fades out and stays away this session.
            fade_out()
            return {'FINISHED'}
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            _state["pressed"] = None
            area.tag_redraw()
        return {'RUNNING_MODAL'}  # While it covers the 3D view, nothing behind it reacts.


def register():
    bpy.utils.register_class(ANGELO_OT_connect_hero)
    watch()


def unregister():
    global _timer
    if _timer is not None and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer = None
    _release()
    bpy.utils.unregister_class(ANGELO_OT_connect_hero)
