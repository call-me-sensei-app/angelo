# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""10,000 synthetic activities reach first/latest through real wheel events; isolated GUI only."""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

assert '--enable-event-simulate' in sys.argv and not bpy.app.background
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default startup with its chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-history-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "chat-history.json"
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


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def line():
    return chat()[0].spaces.active.history[-1]


def scale():
    from angelo_agent import chat_view
    return chat_view.ui_scale()


def send(kind, x, y, value='NOTHING', unicode=None):
    arguments = dict(type=kind, value=value, x=int(round(x)), y=int(round(y)))
    if unicode:
        arguments["unicode"] = unicode
    window().event_simulate(**arguments)


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def pixels(name):
    """The window as drawn, ``(height, width, 4)`` floats, y up; saved as ``name`` for the record."""
    path = OUT / name
    redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    image = bpy.data.images.load(str(path))
    try:
        width, height = image.size
        values = np.empty(width * height * 4, np.float32)
        image.pixels.foreach_get(values)
        return values.reshape(height, width, 4)
    finally:
        bpy.data.images.remove(image)


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
    print("ANGELO_CHAT_HISTORY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def click_action(action, value):
    rect = next(rect for rect, kind, payload in view_state()["hits"] if kind == action and payload == value)
    x, y, w, h = rect
    px, py = chat()[0].x + (x + w / 2) * scale(), chat()[0].y + (y + h / 2) * scale()
    send('LEFTMOUSE', px, py, 'PRESS')
    send('LEFTMOUSE', px, py, 'RELEASE')


def steps():
    from angelo_agent import console, chat_view
    yield lambda: any(console.is_chat(a) for a in window().screen.areas)
    control = controller()
    messages = [{"role": "assistant", "text": "FIRST HISTORY SENTINEL"}]
    messages += [{"role": "activity", "text": "", "call_id": "history-%d" % i,
                  "tool": "angelo_observe_scene", "arguments": {}, "result": {}, "status": "completed"}
                 for i in range(10000)]
    messages += [{"role": "user", "text": "withdrawn", "withdrawn": True} for _ in range(200)]
    messages += [{"role": "assistant", "text": "History detail.\n\n" * 40 + "LATEST HISTORY SENTINEL"}]
    control.state["messages"][:] = messages
    control.version += 1
    chat()[0].tag_redraw()
    yield lambda: view_state().get("history_range", (0, 0, 0))[2] == len(messages)
    for direction, boundary in (("WHEELUPMOUSE", "first"), ("WHEELDOWNMOUSE", "latest")):
        state["phase"] = boundary
        for _ in range(200):
            v = view_state()
            at_page = v["history_range"][0] == 0 if boundary == "first" else v["history_range"][1] == v["history_range"][2]
            offset = chat_view._scroll.get((chat()[0].as_pointer(), control.state["id"]), 0)
            at_pixel = offset >= v.get("max_scroll", 0) if boundary == "first" else offset == 0
            reached = at_page and at_pixel
            if reached:
                break
            chat_view._scroll[(chat()[0].as_pointer(), control.state["id"])] = v.get("max_scroll", 0) if boundary == "first" else 0
            x, y, w, h = v["message_viewport"]
            send(direction, chat()[0].x + (x + w / 2) * scale(), chat()[0].y + (y + h / 2) * scale(), 'PRESS')
            yield 0.08
        check("wheel_reaches_" + boundary, reached)
        if boundary == "latest":
            scroll_key = (chat()[0].as_pointer(), control.state["id"])
            chat_view._scroll[scroll_key] = view_state()["max_scroll"]
            chat()[0].tag_redraw()
            yield 0.2
            hit = next((rect for rect, action, value in view_state()["hits"]
                        if action == "history" and value == "latest"), None)
            check("latest_control_enabled_away_from_bottom", hit is not None)
            if hit:
                x, y, w, h = hit
                px, py = chat()[0].x + (x + w / 2) * scale(), chat()[0].y + (y + h / 2) * scale()
                send('LEFTMOUSE', px, py, 'PRESS')
                send('LEFTMOUSE', px, py, 'RELEASE')
                yield 0.2
            check("latest_click_reaches_actual_bottom", chat_view._scroll.get(scroll_key, 0) == 0)
        pixels("history-" + boundary + ".png")
        sentinel = messages[0] if boundary == "first" else control.state["messages"][-1]
        key = (control.state["id"], id(sentinel))
        rect = view_state().get("message_rects", {}).get(key)
        check(boundary + "_sentinel_has_visible_hit_area", bool(rect))
        if boundary == "first":
            check("spanning_group_is_a_fragment", view_state().get("history_fragments", 0) > 0)
            control.state["messages"].append({"role": "assistant", "text": "APPENDED HISTORY SENTINEL"})
            control.version += 1
            chat()[0].tag_redraw()
            yield 0.2
            check("append_keeps_older_page", view_state()["history_range"][0] == 0)
            viewport = view_state()["message_viewport"]
            before_rect = view_state().get("message_rects", {}).get(key)
            before_gap = viewport[1] + viewport[3] - (before_rect[1] + before_rect[3]) if before_rect else None
            bpy.context.preferences.view.ui_scale = 1.2
            yield 0.3
            check("resize_keeps_first_page", view_state()["history_range"][0] == 0)
            after_rect = view_state().get("message_rects", {}).get(key)
            viewport = view_state()["message_viewport"]
            after_gap = viewport[1] + viewport[3] - (after_rect[1] + after_rect[3]) if after_rect else None
            check("resize_preserves_sentinel_anchor", before_gap is not None and after_gap is not None
                  and abs(before_gap - after_gap) <= 3)
            check("offset_cache_bounded", len(view_state().get("history_offsets", {})) <= 164)
            pixels("history-first-resized.png")
            bpy.context.preferences.view.ui_scale = 1.0
            yield 0.2
            group = next(value for rect, action, value in view_state()["hits"]
                         if action == "expand" and isinstance(value, tuple) and value[:1] == ("group",))
            click_action("expand", group)
            yield 0.2
            check("real_group_click_expands", group in view_state()["expanded"])
            click_action("history", "later")
            yield 0.2
            members = view_state().get("history_group_members", {})
            check("open_group_survives_real_page_click", bool(members)
                  and any(key in view_state()["expanded"] for key in members))
            scroll_key = (chat()[0].as_pointer(), control.state["id"])
            chat_view._scroll[scroll_key] = view_state()["max_scroll"]
            view_state().setdefault("progress_drawn", {}).clear()
            control.busy = True  # Synthetic busy state; no worker or provider is started.
            control.version += 1
            chat()[0].tag_redraw()
            yield 0.2
            group = next(iter(view_state()["history_group_members"]))
            drawn = view_state().get("progress_drawn", {}).get(group)
            check("older_group_draws_done_while_later_work_is_busy", drawn is not None and drawn[2] == "done")
            pixels("history-older-busy.png")
            control.busy = False
            control.version += 1
            chat()[0].tag_redraw()
            yield 0.2
    check("draw_is_clean", not chat_view.last_error)
    check("transcript_complete", len(control.state["messages"]) == len(messages) + 1)


_steps = steps()
_waiting = {}


def tick():
    try:
        if time.monotonic() - started > 400:
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

# Held until Angelo's default start-up (Director's layout, the first tab build, the cover) is over.
bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
