# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An input method types into the composer in the running app: compositions through the window manager's own path.

``Window.ime_simulate`` feeds a composition through ``WM_event_IME_apply``, the function GHOST's input method events
go through, so the window's state, the events and the chat's handler are the real ones. Checked, with a capture each:
the composer with the keyboard holds the window's input method (``ime_owner`` "SCRIPT") and its candidate window at
the drawn caret (``ime_caret``, the caret's band in window pixels); a composition over a selection shows inline in
the selection's place, underlined, with the caret inside it; Backspace while composing edits the composition, never
the draft; the commit replaces the selection, shows in the same frame, and one Undo brings the selection back; the
pointer moving to the 3D view keeps the session (the chat keeps the keyboard); a menu takes the session and the
composer takes it back once the menu closes; Escape lets the keyboard and the session go.

GUI --factory-startup --enable-event-simulate only; no provider, account or network. A real input method (the
system's candidate window) is checked by hand, see doc/angelo/agent_chat.rst "Input methods".
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert '--enable-event-simulate' in sys.argv and not bpy.app.background
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default startup with its chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-composer-ime-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "composer-ime-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "measures": {}, "screenshots": []}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def line():
    return chat()[0].spaces.active.history[-1]


def at(x, y):
    from qa_input import chat_rect   # The chat's points as window pixels at its scale; the text is never mirrored.
    return chat_rect(chat()[1], (x, y, 0, 0), rtl=False)[:2]


def centre():
    x, y, w, h = view_state()["composer"]
    return at(x + w / 2, y + h / 2)


def send(kind, x, y, value='PRESS', **extra):
    if kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
        value = 'NOTHING'
    window().event_simulate(type=kind, value=value, x=int(round(x)), y=int(round(y)), **extra)


def key(kind, **held):
    x, y = centre()
    send(kind, x, y, 'PRESS', **held)
    send(kind, x, y, 'RELEASE', **held)


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def capture(name):
    path = OUT / name
    redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def check(name, condition, **measures):
    state["checks"][name] = bool(condition)
    if measures:
        state["measures"][name] = measures


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_COMPOSER_IME_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def set_text(value, caret, anchor=None):
    from angelo_agent import chat_composer
    chat_composer.set_draft(line(), value, caret)
    view_state()["selection"] = (min(anchor, caret), max(anchor, caret)) if anchor not in (None, caret) else None
    redraw()


def shown():
    return [value.rstrip() for value, _, _ in view_state().get("draft_rows", [])]


def caret_band():
    """The drawn caret's band in window pixels: (x, bottom, height), as the composer placed the session."""
    from angelo_agent import chat_composer, chat_view
    caret = chat_composer.cursor_position(line())
    shown_ime = view_state().get("composer_ime_shown")
    caret = shown_ime.caret if shown_ime else caret
    value, offset, y = next((v, o, y) for v, o, y in reversed(view_state()["draft_rows"]) if o <= caret)
    s = chat_view.ui_scale()
    x, bottom = at(chat_composer.column_x(value, caret - offset, view_state()["font_size"]), y - 3)
    return int(x), int(bottom), s


def primary():
    return {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}


def ime(phase, composition="", cursor=-1, result=""):
    window().ime_simulate(phase=phase, composition=composition, cursor=cursor, result=result)


def steps():
    from angelo_agent import console, ui
    yield lambda: any(console.is_chat(a) for a in window().screen.areas)
    # A chat with rows: an empty chat with no account waits (its field takes no text, so no input method either).
    controller = ui.get_controller()
    controller.state["messages"][:] = [{"role": "user" if i % 2 else "assistant", "text": "Row %d" % i}
                                       for i in range(6)]
    controller.version += 1
    yield lambda: view_state().get("composer", (0, 0, 0, 0))[2] > 0 and not view_state().get("composer_hidden")
    check("the_field_takes_text", not view_state().get("composer_hidden"))
    x, y = centre()
    send('MOUSEMOVE', x, y)
    send('LEFTMOUSE', x, y, 'PRESS')
    send('LEFTMOUSE', x, y, 'RELEASE')
    yield 0.3
    state["measures"]["platform"] = sys.platform   # macOS and Windows: Blender's input method support (manifest).
    state["phase"] = "session"
    set_text("hello world", 11)
    yield 0.2
    redraw()
    check("the_composer_with_the_keyboard_holds_the_input_method", window().ime_owner == "SCRIPT",
          owner=window().ime_owner)
    x_caret, bottom, _ = caret_band()
    placed = tuple(window().ime_caret)
    check("the_candidate_window_is_at_the_drawn_caret", abs(placed[0] - x_caret) <= 1 and abs(placed[1] - bottom) <= 1,
          placed=placed, caret=(x_caret, bottom))

    state["phase"] = "composition"
    set_text("hello world", 11, anchor=6)
    ime('START', "", 0)
    ime('UPDATE', "せかい", 3)
    yield 0.2
    redraw()
    check("the_composition_shows_inline_in_the_selections_place", shown() == ["hello せかい"], shown=shown())
    check("nothing_is_in_the_draft_until_it_is_committed", line().body == "hello world", body=line().body)
    x_caret, bottom, _ = caret_band()
    placed = tuple(window().ime_caret)
    check("the_candidate_window_follows_the_composition", abs(placed[0] - x_caret) <= 1, placed=placed,
          caret=(x_caret, bottom))
    capture("composition.png")
    key('BACK_SPACE')
    yield 0.2
    check("the_input_methods_keys_never_edit_the_draft", line().body == "hello world" and window().is_ime_composing,
          body=line().body)

    state["phase"] = "commit"
    ime('UPDATE', "", -1, result="世界")
    yield 0.05   # One event round, no forced redraw: the handler redraws the composer itself.
    check("the_commit_replaces_the_selection", line().body == "hello 世界", body=line().body)
    ime('END')
    yield 0.2
    capture("committed.png")
    check("the_commit_is_drawn", shown() == ["hello 世界"], shown=shown())
    key('Z', **primary())
    yield 0.3
    check("one_undo_brings_the_selection_back", line().body == "hello world"
          and view_state().get("selection") == (6, 11), body=line().body, selection=view_state().get("selection"))

    state["phase"] = "focus"
    view3d = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    send('MOUSEMOVE', view3d.x + view3d.width / 2, view3d.y + view3d.height / 2)
    yield 0.3
    redraw()
    check("the_pointer_elsewhere_keeps_the_session", window().ime_owner == "SCRIPT",
          owner=window().ime_owner)
    send('MOUSEMOVE', *centre())
    with bpy.context.temp_override(window=window(), area=chat()[0], region=chat()[1]):
        bpy.ops.wm.call_menu(name="TOPBAR_MT_file")
    yield 0.4
    check("a_menu_takes_the_session", window().ime_owner != "SCRIPT", owner=window().ime_owner)
    key('ESC')   # Closes the menu.
    yield 0.4
    redraw()
    check("the_composer_takes_it_back_after_the_menu", window().ime_owner == "SCRIPT",
          owner=window().ime_owner)
    key('ESC')   # The composer lets the keyboard go.
    yield 0.3
    redraw()
    check("escape_lets_the_session_go", window().ime_owner != "SCRIPT", owner=window().ime_owner)


_steps = steps()
_waiting = {}


def tick():
    try:
        if time.monotonic() - started > 240:
            raise RuntimeError("Timed out in " + state["phase"])
        condition = _waiting.get("condition")
        if condition is not None and not condition():
            return 0.1
        step = next(_steps)
        _waiting["condition"] = step if callable(step) else None
        return 0.1 if callable(step) else step
    except StopIteration:
        finish()
    except Exception as error:
        finish(error)
    return None


sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
