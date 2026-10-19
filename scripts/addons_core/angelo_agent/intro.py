# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""First-launch welcome: what Director and the standard editor are, how to switch, and demos.

Angelo starts in Director; a separate
welcome screen (not Blender's splash) tells new users what the two modes are
and how to switch between them, and links to recorded demos ("See it in
action", ``first_run.DEMOS``). It stays reachable from Help and from the
Director / Manual menu.

The screen is drawn by ``welcome_screen.py`` over the largest 3D view, while
the chat beside it stays usable: this module's modal handler takes only the
person's pointer and keys over that 3D view. It passes on, wherever the pointer
is, what is not input (timers, window events), Blender's motionless hover
refresh (a quit waits for it: ``input_policy.refresh_move``) and the quit key.
Escape, Return, the close button, Start creating or a click outside the card
dismisses it; Try the standard editor switches through ``director.request_mode``.

It opens by itself once, after the default Director layout has settled (an
earlier draw would be lost in the layout change), and only on first launch: no
welcome shown before and no mode saved by an older build
(``first_run.should_show_intro``). On a first launch the setup card comes first
(``splash_setup``: Blender's splash art, then the setup card, then this welcome,
each once) and the welcome follows when it closes.
``ANGELO_DISABLE_INTRO=1`` turns both off for automation; the live-evaluation
harness never reaches them because it disables the default layout.
"""

from pathlib import Path

import bpy
from bpy.types import Operator

from . import (canvas_controls, canvas_rtl, connect_hero, first_run, input_policy, product_env, splash_setup,
               welcome_screen)
from .display_text import Text, shown
from .ui_text import rpt_
from .studio_state import atomic_json, read_json

STATE_FILE = "intro.json"
_timer = None
_timer_startup = False  # The scheduled opening is the session's startup (``file_loaded``).
_auto_opened = False  # Startup and load_post both reach the scheduler; open by itself once per session.
opened = 0  # Times the intro opened this session; window fixtures read it.


def state_path():
    from .ui import state_directory
    return Path(state_directory()) / STATE_FILE


def seen():
    value = read_json(state_path(), {})
    return isinstance(value, dict) and value.get("seen") is True


def remember(choice):
    atomic_json(state_path(), {"seen": True, "choice": choice})


def schedule_first_launch(startup=False):
    """Once the default layout has settled: at startup, Angelo's card in place of Blender's splash (the setup
    card on a first launch, the start panel for a returning user, ``splash_setup``), then the welcome if this is
    the first launch. Each opens at most once; Blender's splash itself is never reopened."""
    global _timer, _timer_startup
    from . import director
    disabled = product_env.flag("DISABLE_INTRO")
    welcome = not _auto_opened and first_run.should_show_intro(seen(), director.mode_path().exists(), disabled)
    card = startup and not disabled and splash_setup.shows_on_startup()
    setup = card and not splash_setup.preferences_saved()
    start = card and not setup and not welcome
    if _timer is not None or not (welcome or setup or start):
        if startup:
            splash_setup.startup_settled()
        return

    def open_when_ready():
        global _timer, _auto_opened
        from . import chat_view, console, ui
        from .input_policy import interaction_active
        windows = [w for w in bpy.context.window_manager.windows if w.screen and not w.screen.is_temporary]
        if not windows:
            _timer = None
            splash_setup.startup_settled()
            return None
        if director._timer is not None or ui._layout_timer is not None or interaction_active(windows[0]):
            return .2
        if (setup or start) and windows[0].as_pointer() not in chat_view._modals and any(
                console.is_chat(a) for a in windows[0].screen.areas):
            return .2  # The chat's input handler first: a handler added later would sit above the card and take its keys.
        _timer = None
        show_welcome = welcome and not (_auto_opened or seen())
        window = windows[0]
        with bpy.context.temp_override(window=window):
            if setup or start:
                splash_setup.close_popups(window)  # Blender's splash, where no workspace switch closed it.
            splash_setup.startup_settled()
            if setup and splash_setup.open_setup(window, show_welcome):
                return None  # The welcome follows the setup card (``open_after_setup``).
            if start and splash_setup.open_start(window):
                return None
            if show_welcome:
                _auto_opened = True
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
        return None

    _timer, _timer_startup = open_when_ready, startup
    bpy.app.timers.register(open_when_ready, first_interval=.3)


def file_loaded():
    """After a file load (the default layout's ``load_post``). Blender drops non-persistent timers on a load, this
    module's scheduling with them, and ``card_host`` closes a visible card as lost: forget the dropped timer so the
    next ``schedule_first_launch`` is not refused. Returns whether Angelo's startup was still owed (its scheduling
    dropped, or the setup card closed unanswered), so the layout decides it again for the opened file: a .blend
    opened from the Finder at first launch still gets the setup card, then the welcome."""
    global _timer, _timer_startup
    owed = splash_setup.take_setup_lost()
    if _timer is not None and not bpy.app.timers.is_registered(_timer):
        owed = owed or _timer_startup
        _timer, _timer_startup = None, False
    return owed


def open_after_setup(window):
    """The welcome screen, once, after the setup card closed (Continue or Escape)."""
    global _auto_opened
    if _auto_opened or seen() or window is None:
        return
    _auto_opened = True
    bpy.ops.angelo.intro('INVOKE_DEFAULT')



_open = {}  # {"window", "area"} pointers while the welcome is shown; "plan", "hover", "pressed" for input.
_handler = None


def is_open():
    return bool(_open)


def open_demo(url):
    """One place that leaves the app, so window fixtures can record it instead of opening a browser."""
    bpy.ops.wm.url_open(url=url)


def _target_area(window):
    areas = [a for a in window.screen.areas if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height, default=None)


def _draw():
    from . import chat_view
    area, region = bpy.context.area, bpy.context.region
    if not _open or area is None or area.as_pointer() != _open.get("area") or region is None:
        return
    scale = bpy.context.preferences.system.ui_scale
    plan = welcome_screen.layout(chat_view, region.width, region.height, scale, _open.get("hover"))
    _open["plan"] = plan
    connect_hero.paint_beneath(area, region)  # The showcase, not the empty scene, sits under the welcome's scrim.
    import gpu
    gpu.state.blend_set('ALPHA')
    canvas_rtl.begin(region.width)  # Mirrored in a right-to-left language.
    try:
        welcome_screen.draw(chat_view, plan)
        # The screen colours its own hover; pressed and keyboard focus come from the shared canvas states.
        canvas_controls.draw(chat_view, plan["hits"], _open.get("hover"), _open.get("pressed"), _open.get("focus"),
                             scale=plan["scale"], layers=False)
    finally:
        canvas_rtl.end()
    gpu.state.blend_set('NONE')


def _close(context, choice):
    area = _open.get("area")
    _open.clear()
    for window in context.window_manager.windows:
        for candidate in window.screen.areas:
            if candidate.as_pointer() == area:
                candidate.tag_redraw()
    try:
        remember(choice)
    except OSError:
        pass  # The welcome may then show again next launch; nothing else depends on it.


class ANGELO_OT_intro(Operator):
    bl_idname = "angelo.intro"
    bl_label = "Welcome to Angelo"
    bl_description = "What Director and the standard editor are, how to switch between them, and short demos"

    def invoke(self, context, event):
        global opened, _handler
        area = _target_area(context.window)
        if area is None:
            self.report({'INFO'}, rpt_("Open a 3D Viewport to see the welcome screen"))
            return {'CANCELLED'}
        welcome_screen.prepare_banner()
        if _handler is None:
            _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        _open.clear()
        self.token = object()  # A reopened welcome retires the previous handler.
        _open.update(window=context.window.as_pointer(), area=area.as_pointer(), hover=None, pressed=None, focus=None,
                     token=self.token)
        opened += 1
        splash_setup.sequence.append("welcome")
        context.window_manager.modal_handler_add(self)
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if not _open or _open.get("token") is not self.token:
            return {'FINISHED', 'PASS_THROUGH'}  # Closed: whatever this event is, it belongs to what is below.
        passes = input_policy.overlay_passes(event, context.window_manager)  # Timers, window events, the quit key.
        if passes or context.window.as_pointer() != _open["window"]:
            return {'PASS_THROUGH'}
        area = next((a for a in context.window.screen.areas if a.as_pointer() == _open["area"]), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if region is None:  # The layout changed under it (mode switch, file load): close quietly.
            _close(context, None)
            return {'FINISHED', 'PASS_THROUGH'}
        inside = region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height
        if not inside or "plan" not in _open:
            return {'PASS_THROUGH'}  # The chat and everything else stay usable.
        x = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)
        target = welcome_screen.hit(_open["plan"], x, event.mouse_y - region.y)
        control = target if target and target[0] != "outside" else None
        if event.type == 'MOUSEMOVE':
            from . import chat_view, console
            # This screen takes the move, so the chat never sees the pointer leave: clear its hover here.
            canvas_controls.leave_areas(context.window, chat_view._views, console.is_chat)
            _open["cursor"] = 'HAND_POINT' if control else 'DEFAULT'  # Kept for window fixtures to read.
            context.window.cursor_set(_open["cursor"])
            if control != _open["hover"]:
                _open["hover"] = control
                area.tag_redraw()
            # Blender's own refresh (no motion) goes on: a quit requested before the welcome opened waits for it.
            return {'PASS_THROUGH'} if input_policy.refresh_move(event) else {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type == 'TAB':  # Keyboard focus, as on the console.
            focus = {"hits": _open["plan"]["hits"], "focus": _open.get("focus")}
            canvas_controls.move_focus(focus, event.shift)
            _open["focus"] = focus["focus"]
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and _open.get("focus"):
            return self.activate(context, *_open["focus"])
        if event.value == 'PRESS' and event.type in {'ESC', 'RET', 'NUMPAD_ENTER'}:
            _close(context, "DIRECTOR" if event.type != 'ESC' else None)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            _open["pressed"], _open["focus"] = target, None
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pressed, _open["pressed"] = _open["pressed"], None
            area.tag_redraw()
            if target != pressed or target is None:
                return {'RUNNING_MODAL'}
            return self.activate(context, *target)
        return {'RUNNING_MODAL'}  # While it covers the 3D view, nothing behind it reacts.

    def activate(self, context, action, value):
        if action == "demo":
            open_demo(first_run.DEMOS[value][1])
            return {'RUNNING_MODAL'}
        _close(context, "MANUAL" if action == "standard" else "DIRECTOR" if action == "start" else None)
        if action == "standard":
            from . import director
            try:
                director.request_mode(context.window, "MANUAL")
            except (RuntimeError, ValueError) as error:
                self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
        return {'FINISHED'}


def draw_help(self, context):
    self.layout.separator()
    self.layout.operator("angelo.intro", icon='INFO')


def register():
    bpy.utils.register_class(ANGELO_OT_intro)
    bpy.types.TOPBAR_MT_help.append(draw_help)
    splash_setup.register()
    connect_hero.register()  # After the welcome: the showcase waits while the welcome is open.


def unregister():
    global _timer, _handler
    if _timer is not None and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer = None
    connect_hero.unregister()
    splash_setup.unregister()
    _open.clear()
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    welcome_screen.release()
    bpy.types.TOPBAR_MT_help.remove(draw_help)
    bpy.utils.unregister_class(ANGELO_OT_intro)
