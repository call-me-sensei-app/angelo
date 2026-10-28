# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hosts the AI Providers page on Angelo's canvas inside the Preferences content region.

* **Drawing**: a POST_PIXEL handler on ``SpacePreferences`` (region ``WINDOW``) draws the page while the
  AI Providers section is shown (``preferences_ui.is_selected``).
* **Scrolling**: the section's panel draws only an invisible spacer as tall as the page (``draw_panel``), so
  Blender's View2D owns scrolling, the scrollbar and trackpad momentum; the canvas follows ``view2d``.
* **Input**: one window modal per Preferences window, started from a timer (a draw callback cannot invoke
  operators). It takes presses on the page's controls and Tab / Return / Space / Escape over the page; the
  rest passes through. An open ⋯ menu or connection sheet takes the page region's events first.
* **Actions** run the same operators the stock page used (``act``); only the presentation changed.
"""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from .ui_text import iface_
from .input_policy import modal_options, overlay_passes, refresh_move
from . import canvas_rtl, card_kit as kit, preferences_ui, providers_page_draw, providers_page_layout, providers_page_model
from . import live_windows, providers_page_external, providers_page_rail

_handler = None
_plans = {}      # region pointer -> (plan, page height)
_ui = {}         # region pointer -> card_kit.UiState
_keyboard = set()  # region pointers whose focus ring is shown
_modals = set()  # window pointers with a running input modal
_view = None
SPACER_UNIT = 20  # UI_UNIT_Y at a UI scale of 1
states_drawn = []  # Page model summaries drawn, newest last (bounded): fixtures assert on them.
_menu = None       # {"id", "confirm", "plan"}: a connection row's open ⋯ menu (providers_menu).
_sheet = None      # The connection sheet open over the page (providers_sheet.ConnectionSheet), if any.
_sheet_ui = kit.UiState()
_sheet_plan = None
_sheet_keyboard = False


class _SheetHost:
    """What the sheet's ``act`` may do (the same calls as ``card_host.Host``)."""

    def __init__(self, card):
        self.card = card

    def redraw(self):
        preferences_ui.redraw()

    def close(self, how="done"):
        close_sheet(how, self.card)

    def after(self, seconds, function):
        card = self.card

        def run():
            return function() if _sheet is card else None
        bpy.app.timers.register(run, first_interval=seconds)


def open_sheet(card):
    """Show ``card`` (a ``providers_sheet.ConnectionSheet``) over the page; it replaces an open one."""
    global _sheet, _sheet_plan, _sheet_keyboard
    if _sheet is not None:
        _sheet.closed("replaced")
    _sheet, _sheet_plan, _sheet_keyboard = card, None, False
    _sheet_ui.hover = _sheet_ui.pressed = _sheet_ui.focus = None
    card.opened(_SheetHost(card))
    preferences_ui.redraw()
    if not bpy.app.timers.is_registered(_watch_sheet):
        bpy.app.timers.register(_watch_sheet, first_interval=1.0)


def _watch_sheet():
    """Close the sheet (wiping its key) once no Preferences window shows the page: a closed window never says so."""
    if _sheet is None:
        return None
    shown = preferences_ui.is_selected() and any(a.type == 'PREFERENCES'
                                                 for _w, a in live_windows.window_areas(bpy.context.window_manager))
    if not shown:
        close_sheet("lost")
        return None
    return 1.0


def close_sheet(how="done", card=None):
    global _sheet, _sheet_plan
    if _sheet is None or (card is not None and _sheet is not card):
        return
    closing, _sheet, _sheet_plan = _sheet, None, None
    closing.closed(how)
    preferences_ui.redraw()


def _canvas_view():
    global _view
    if _view is None:
        from .card_host import _View
        _view = _View()
    return _view


def draw_panel(layout, context):
    """The section's panel body: True when the canvas page draws it (an interactive Preferences window)."""
    if bpy.app.background or not isinstance(layout, bpy.types.UILayout):
        return False
    region = context.region
    if region is None:
        return False
    ensure_handler()
    scale = context.preferences.system.ui_scale
    # Laid out here too, so the spacer is the page's height in the same redraw. Never tag a redraw to catch up:
    # redrawing the region again closes a dialog opened over it (the API-key form).
    built = _plan(context, region, 0.0)
    height = built[0].card[3] if built else 1200
    column = layout.column()
    column.scale_y = max(1.0, height / (SPACER_UNIT * scale))
    column.label(text="")
    window = context.window
    if window is not None and window.as_pointer() not in _modals:
        pointer = window.as_pointer()
        bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, _start_input), first_interval=0)
    return True


def ensure_handler():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpacePreferences.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def _top(region):
    return region.view2d.view_to_region(0, 0, clip=False)[1]


def _draw():
    global _sheet_plan
    context = bpy.context
    region = context.region
    if region is None or not preferences_ui.is_selected():
        return
    pointer = region.as_pointer()
    state = _ui.setdefault(pointer, kit.UiState())
    state.time = time.monotonic()
    if pointer not in _keyboard:
        state.focus = None
    built = _plan(context, region, _top(region), state)
    if built is None:
        return
    plan, model = built
    _plans[pointer] = (plan, plan.card[3])
    _remember(model)
    view = _canvas_view()
    menu = _menu_plan(context, region, plan, view)
    sheet = None
    if _sheet is not None:
        _sheet_ui.time = state.time
        _sheet_ui.focus = _sheet_ui.focus if _sheet_keyboard else None
        sheet = _sheet_plan = _sheet.layout(view, region.width, region.height, context.preferences.system.ui_scale, _sheet_ui)
    canvas_rtl.begin(region.width)
    try:
        providers_page_draw.draw(view, plan, region.width, region.height, state.time)
        from . import card_draw
        for overlay in (menu, sheet):   # The open ⋯ menu, then a connection sheet over everything.
            if overlay is not None:
                card_draw.draw(view, overlay)
    finally:
        canvas_rtl.end()
    if _sheet is not None and _sheet.animated and not bpy.app.timers.is_registered(_animate):
        bpy.app.timers.register(_animate, first_interval=0.08)
    import gpu
    gpu.state.blend_set('NONE')
    if getattr(plan, "animated", False) and not bpy.app.timers.is_registered(_animate):
        bpy.app.timers.register(_animate, first_interval=0.08)


def _menu_plan(context, region, plan, view):
    """Lay out the open ⋯ menu under its button (closed when its connection or button is gone)."""
    global _menu
    if _menu is None:
        return None
    from . import providers_menu, saved_keys_ui, ui
    record = ui.connections().records.get(_menu["id"])
    anchor = next((rect for rect, action, value in plan.hits if action == "menu" and value == _menu["id"]), None)
    if record is None or anchor is None:
        _menu = None
        return None
    confirm = (record["name"], iface_(saved_keys_ui.TEXT.remove_description)) if _menu["confirm"] else None
    _menu["plan"] = providers_menu.layout(view, anchor, providers_menu.items(ui, record), confirm,
                                          (region.width, region.height), context.preferences.system.ui_scale,
                                          _menu.setdefault("ui", kit.UiState()))
    return _menu["plan"]


def _animate():
    """One more frame for a spinning chip (only while one spins; never while the page is hidden)."""
    if preferences_ui.is_selected() and (any(getattr(plan, "animated", False) for plan, _ in _plans.values())
                                         or (_sheet is not None and _sheet.animated)):
        preferences_ui.redraw()
    return None


_model_cache = [0.0, None]   # (when, model): the panel's spacer and the canvas share one build per redraw.


def _plan(context, region, top, state=None):
    """``(plan, model)`` for the region with the page's top edge at ``top``; None while the add-on is half-registered."""
    from . import ui
    now = time.monotonic()
    model = _model_cache[1] if now - _model_cache[0] < 0.05 else None
    if model is None:
        try:
            model = providers_page_model.build(ui, ui.preferences())
        except Exception:   # Enable/disable in progress: draw nothing this frame.
            import traceback
            traceback.print_exc()
            return None
        _model_cache[:] = [now, model]
    scale = context.preferences.system.ui_scale
    return providers_page_layout.layout(_canvas_view(), model, region.width, top, scale, state or kit.UiState()), model


def _remember(model):
    summary = {"sections": [s["title"] for s in model.get("sections", ())],
               "rows": [r.get("name") for s in model.get("sections", ()) for r in s.get("rows", ())],
               "chips": [r["chip"][0] for s in model.get("sections", ()) for r in s.get("rows", ()) if r.get("chip")],
               "team": (model.get("team") or {}).get("title"), "slim_team": bool((model.get("team") or {}).get("slim")),
               "attention": [n["title"] for n in model.get("attention", ())], "add": model["hero"].get("add")}
    if not states_drawn or states_drawn[-1] != summary:
        states_drawn.append(summary)
        del states_drawn[:-16]


def _start_input(window):
    if window is None or window.as_pointer() in _modals:
        return None
    area = next((a for a in live_windows.areas(window) if a.type == 'PREFERENCES'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if region is None:
        return None
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.angelo.providers_page_input('INVOKE_DEFAULT')
    except RuntimeError:
        pass
    return None


SIMPLE_OPERATORS = {"team_sign_in": ("team_sign_in", 'INVOKE_DEFAULT', None), "team_sign_out": ("team_sign_out", 'INVOKE_DEFAULT', None),
                    "team_cancel": ("team_cancel_sign_in", 'EXEC_DEFAULT', None), "team_refresh": ("team_refresh", 'EXEC_DEFAULT', None),
                    "team_copy_link": ("team_copy_sign_in_link", 'EXEC_DEFAULT', None),
                    "team_console": ("team_open_console", 'EXEC_DEFAULT', "page"), "enhancer": ("enhancer_settings", 'INVOKE_DEFAULT', None)}
MENU_OPERATORS = {"menu_cancel_signin": ("cancel_signin", "mode"), "menu_reconnect": ("connection_signin", "connection_id"),
                  "menu_load_key": ("connection_load_key", "connection_id"), "menu_check": ("connection_check", "connection_id")}


def _open_menu(record_id):
    global _menu
    _menu = {"id": record_id, "confirm": False} if record_id and not (_menu and _menu["id"] == record_id) else None
    preferences_ui.redraw()


def plan_for(region):
    return _plans.get(region.as_pointer(), (None, 0))[0]


def act(action, value, window, area, region):
    """Run one page action through the existing operators (with the Preferences region as context)."""
    from . import ui
    ops = bpy.ops.angelo
    prefs = ui.preferences()
    with bpy.context.temp_override(window=window, area=area, region=region):
        from .providers_sheet import ConnectionSheet
        if action == "add":
            open_sheet(ConnectionSheet(group=value or providers_page_model.CODING))
        elif action == "edit":
            _open_menu(None)
            if ui.connections().records.get(value):
                open_sheet(ConnectionSheet(connection_id=value))
        elif action == "menu":
            _open_menu(value)
        elif action == "menu_close":
            _open_menu(None)
        elif action == "menu_remove":
            _menu["confirm"] = True
        elif action == "menu_remove_confirm":
            record_id = _menu["id"]
            _open_menu(None)
            ops.connection_remove('EXEC_DEFAULT', connection_id=record_id)
        elif action in MENU_OPERATORS:
            _open_menu(None)
            name, prop = MENU_OPERATORS[action]
            getattr(ops, name)('EXEC_DEFAULT', **{prop: value})
        elif action == "reconnect":
            ops.connection_signin('INVOKE_DEFAULT', connection_id=value)
        elif action == "quick_connect":
            ops.quick_connect('EXEC_DEFAULT', mode=value)
        elif action == "dismiss_suggestion":
            ops.dismiss_native_suggestion('EXEC_DEFAULT', mode=value)
        elif action in SIMPLE_OPERATORS:   # Team sign-in and AI Enhance: the operators the stock page called.
            name, how, prop = SIMPLE_OPERATORS[action]
            getattr(ops, name)(how, **({prop: value} if prop else {}))
        elif action in providers_page_external.ACTIONS:   # External AI apps and Blender MCP (external_ui).
            providers_page_external.act(ops, action, value)
        elif action in providers_page_rail.ACTIONS:   # Where the Sessions panel stands, and every 3D view redrawn.
            providers_page_rail.act(action, value)
        elif action == "enable":
            bpy.ops.preferences.addon_enable(module="angelo_agent")
        elif action in ("toggle", "pref") and prefs is not None:   # A switch, or a choice: (preference, its value).
            name, new = (value, not getattr(prefs, value)) if action == "toggle" else value
            setattr(prefs, name, new)
            bpy.context.preferences.is_dirty = True
    _model_cache[:] = [0.0, None]
    preferences_ui.redraw()


class ANGELO_OT_providers_page_input(Operator):
    bl_idname = "angelo.providers_page_input"
    bl_label = "AI Providers Page"
    bl_description = "Pointer and keyboard input for the AI Providers page in Preferences"
    bl_options = {'INTERNAL'} | modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        window = context.window
        if window is None or window.as_pointer() in _modals:
            return {'CANCELLED'}
        self.window = window.as_pointer()
        _modals.add(self.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self):
        _modals.discard(self.window)
        return {'FINISHED', 'PASS_THROUGH'}

    def modal(self, context, event):
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if overlay_passes(event, context.window_manager):   # Timers, window events and the quit key.
            return {'PASS_THROUGH'}
        area = next((a for a in live_windows.areas(window) if a.type == 'PREFERENCES'), None)
        if area is None or not preferences_ui.is_selected():
            return self._finish()
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        plan = plan_for(region) if region else None
        if plan is None:
            return {'PASS_THROUGH'}
        state = _ui.setdefault(region.as_pointer(), kit.UiState())
        over = region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height
        if not over:
            if state.hover or state.pressed:
                state.hover = state.pressed = None
                area.tag_redraw()
            return {'PASS_THROUGH'}
        x, y = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), event.mouse_y - region.y
        if _sheet is not None:
            return self._sheet_event(event, window, area, x, y)
        if _menu is not None and _menu.get("plan") is not None:
            used = self._menu_event(event, window, area, region, x, y)
            if used is not None:
                return used
        target = kit.hit_test(plan, x, y)
        if event.type == 'MOUSEMOVE':
            if target != state.hover:
                state.hover = target
                window.cursor_set('HAND' if target else 'DEFAULT')
                area.tag_redraw()
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            state.pressed = target
            _keyboard.discard(region.as_pointer())
            area.tag_redraw()
            return {'RUNNING_MODAL'} if target else {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pressed, state.pressed = state.pressed, None
            area.tag_redraw()
            if pressed is not None and pressed == target:
                window.cursor_set('DEFAULT')
                act(target[0], target[1], window, area, region)
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'} if pressed else {'PASS_THROUGH'}
        if event.value == 'PRESS' and event.type == 'TAB' and plan.focus_order:
            order = plan.focus_order
            index = order.index(state.focus) if state.focus in order else -1
            state.focus = order[(index + (-1 if event.shift else 1)) % len(order)]
            _keyboard.add(region.as_pointer())
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and region.as_pointer() in _keyboard \
                and state.focus in plan.focus_order:
            act(state.focus[0], state.focus[1], window, area, region)
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type == 'ESC' and region.as_pointer() in _keyboard:
            _keyboard.discard(region.as_pointer())
            state.focus = None
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _menu_event(self, event, window, area, region, x, y):
        """The open ⋯ menu takes clicks on itself and closes on a click elsewhere or Escape; None: not its event."""
        menu = _menu
        ui, plan = menu["ui"], menu["plan"]
        target = kit.hit_test(plan, x, y)
        if event.type == 'MOUSEMOVE':
            if target != ui.hover:
                ui.hover = target
                area.tag_redraw()
            if target is not None or kit.inside(plan.card, x, y):
                window.cursor_set('HAND' if target else 'DEFAULT')
                return {'PASS_THROUGH'}
            return None
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            ui.pressed = target
            if target is None and not kit.inside(plan.card, x, y):
                _open_menu(None)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pressed, ui.pressed = ui.pressed, None
            if pressed is not None and pressed == target:
                act(pressed[0], pressed[1], window, area, region)
            return {'RUNNING_MODAL'}
        if event.type == 'ESC' and event.value == 'PRESS':
            _open_menu(None)
            return {'RUNNING_MODAL'}
        return None

    def _sheet_event(self, event, window, area, x, y):
        """A modal sheet owns the page region: pointer, keys and wheel (it fits the region, so nothing scrolls)."""
        global _sheet_keyboard
        card, plan, ui = _sheet, _sheet_plan, _sheet_ui
        host = _SheetHost(card)
        if plan is None:
            return {'RUNNING_MODAL'}
        target = kit.hit_test(plan, x, y)
        if event.type == 'MOUSEMOVE':
            if target != ui.hover:
                ui.hover = target
                window.cursor_set('HAND' if target else 'DEFAULT')
                area.tag_redraw()
            return {'PASS_THROUGH'} if refresh_move(event) else {'RUNNING_MODAL'}   # A quit waits for a refresh.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            ui.pressed, _sheet_keyboard = target, False
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pressed, ui.pressed = ui.pressed, None
            if pressed is not None and pressed == target:
                card.act(pressed[0], pressed[1], host)
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        if card.key_event(event, host):
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        order = plan.focus_order
        if event.type == 'TAB' and order:
            index = order.index(ui.focus) if ui.focus in order else -1
            ui.focus = order[(index + (-1 if event.shift else 1)) % len(order)]
            _sheet_keyboard = True
        elif event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'}:
            focused = ui.focus if _sheet_keyboard and ui.focus in order else None
            if focused:
                card.act(focused[0], focused[1], host)
            elif event.type != 'SPACE':
                card.act(card.default_action, None, host)
        elif event.type == 'ESC':
            card.act(card.cancel_action, None, host)
        area.tag_redraw()
        return {'RUNNING_MODAL'}


@persistent
def _load_pre(*_args):
    close_sheet("lost")   # The typed key never outlives the file it was typed over.


def register():
    bpy.utils.register_class(ANGELO_OT_providers_page_input)
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _handler, _view
    close_sheet("lost")
    if bpy.app.timers.is_registered(_watch_sheet):
        bpy.app.timers.unregister(_watch_sheet)
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    if _handler is not None:
        bpy.types.SpacePreferences.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _plans.clear()
    _ui.clear()
    _modals.clear()
    _view = None
    bpy.utils.unregister_class(ANGELO_OT_providers_page_input)
