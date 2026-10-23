# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-time show/hide chat hint and the labelled Chat toggle in the running app (``chat_hint``, ``chat_toggle``).

Real default startup, real pointer clicks and keys (``Window.event_simulate``). No CLI, account, network or inference.

Covered, each with a screenshot: Director shows no hint; the first switch to Manual mode shows *Show or hide the chat*
under the top bar's Chat button with its arrow pointing at it (the recorded button rectangle is checked against the
screenshot's pixels), naming the shortcut as bound and the button's place ("next to Manual"); its Hide chat button hides the chat and the card
becomes *The chat is hidden*; its Show chat button brings the chat back, the card closes and both hints are
remembered in the state folder; hiding again shows nothing; with only the first hint remembered, Blender's own
Close Area (not the toggle) shows *The chat is hidden*, Ctrl+Option+N pressed while it shows brings the chat back
(a corner card never makes the dock wait) and closes it, and on another Close Area Got it dismisses and remembers it;
with nothing remembered, Director still shows nothing. The top bar's toggle carries the word Chat (screenshots).

Run through ``run_native_suites.py --gui --include test_blender_chat_hint``; check ``chat-hint.json`` for ``success``
and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
os.environ["ANGELO_ENABLE_CHAT_HINTS"] = "1"   # The runner turns notices off for every suite; this one tests a hint.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-hint-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "chat-hint.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "plans": {}}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat_state():
    from angelo_agent import chat_toggle
    return chat_toggle.chat_state(window(), bpy.context.window_manager.windows)


def card():
    from angelo_agent import card_host
    return card_host.active_card() if card_host.is_active("chat_hint") else None


def memory():
    from angelo_agent import chat_hint
    path = chat_hint._path()
    return json.loads(path.read_text()) if path.exists() else {}


def labels():
    from angelo_agent import card_host
    plan = card_host._active.plan if card_host._active else None
    return [op[2] for op in plan.ops if op[0] == "button"] if plan else []


def texts():
    from angelo_agent import card_host
    plan = card_host._active.plan if card_host._active else None
    return " ".join(op[1] for op in plan.ops if op[0] == "text") if plan else ""


def card_rect():
    """The card's rectangle in window pixels, and its 3D view's."""
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x, y, w, h = active.plan.card
    return (region.x + x, region.y + y, w, h), (area.x, area.y, area.width, area.height)


def click(action):
    """Move to the card's ``action`` control and click it, as a person would."""
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, _v in active.plan.hits if a == action)
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def record_top_bar():
    """Keep the top bar's right-hand region as it draws the toggle, to sample the drawn button's pixels."""
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):   # The compact flags too (top_bar_fit).
        state["top_bar"] = (context.region.y, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def anchored():
    """Where the card stands relative to the top bar's Chat button: its area, and its arrow's tip."""
    from angelo_agent import card_host, chat_toggle
    rect = chat_toggle.anchors.get(window().as_pointer())
    active = card_host._active
    if rect is None or active is None or active.plan is None:
        return {"rect": rect}
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    cx = (rect[0] + rect[1]) / 2
    arrows = [op[2] for op in active.plan.ops if op[0] == "kit" and op[1] == "chat_hint_arrow"]
    tip = (region.x + arrows[0][0] + arrows[0][2] / 2) if arrows else None
    return {"rect": rect, "area": area.type, "under_the_button": region.x <= cx < region.x + region.width,
            "arrow_tip_x": tip, "button_x": cx, "points_at_it": tip is not None and abs(tip - cx) <= 2}


def button_pixels(path, pressed):
    """Whether the screenshot shows the pressed Chat button inside the recorded rectangle (theme's pressed colour)."""
    from angelo_agent import chat_toggle
    left, right, bottom = chat_toggle.anchors[window().as_pointer()]
    y = int(state["top_bar"][0] + state["top_bar"][1] / 2)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width = image.size[0]
        pixels = image.pixels[:]
        want = bpy.context.preferences.themes[0].user_interface.wcol_tool.inner_sel
        def at(x):
            i = (y * width + int(x)) * 4
            return pixels[i:i + 3]
        close = lambda c: sum(abs(a - b) for a, b in zip(c, want[:3])) < 0.25
        # The fill across the whole button: glyph and label cover part of it, so much of the row (never two sampled
        # points, which a 1 px rounding of the anchor put on the glyph's edge) is the pressed colour.
        row = [at(x) for x in range(int(left) + 2, int(right) - 2)]
        share = sum(1 for c in row if close(c)) / max(1, len(row))
        outside = at(left - 6)
        state["button_pixels"] = {"pressed_share": round(share, 3), "outside": [round(v, 3) for v in outside],
                                  "pressed": list(want[:3])}
        return share >= 0.4 and not close(outside)
    finally:
        bpy.data.images.remove(image)


def reports():
    """The window's latest reports (an operator's refusal says why)."""
    return [r.message for r in getattr(bpy.context.window_manager, "reports", [])][-5:] if hasattr(bpy.context.window_manager, "reports") else []


def press_shortcut():
    view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    x, y = view.x + view.width // 3, view.y + view.height // 3
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='N', value='PRESS', x=x, y=y, ctrl=True, alt=True)
    window().event_simulate(type='N', value='RELEASE', x=x, y=y, ctrl=True, alt=True)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CHAT_HINT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    if time.monotonic() - state["waiting"] > limit:
        state["checks"]["timed_out_in_" + phase] = False
        state.pop("waiting", None)
        return True
    return False


def hold(phase, seconds):
    """True once ``seconds`` passed in this phase (nothing may appear meanwhile)."""
    state.setdefault("held", time.monotonic())
    if time.monotonic() - state["held"] < seconds:
        return False
    state.pop("held", None)
    return True


def tick():
    try:
        assert time.monotonic() - started < 240, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import chat_hint, director, ui
        settled = qa_boot.started()
        if phase == "boot":
            if not settled or not chat_state()[2]:
                return .2
            chat_hint._path().unlink(missing_ok=True)
            record_top_bar()
            state["phase"] = "director_quiet"
            return .2
        if phase == "director_quiet":
            if card() is not None:
                checks["director_shows_no_hint"] = False
            if not hold(phase, 3.0):
                return .2
            checks.setdefault("director_shows_no_hint", True)
            director.request_mode(window(), "MANUAL")
            state["phase"] = "manual_hint"
            return .5
        if phase == "manual_hint":
            if not wait(phase, lambda: settled and card() is not None and labels(), limit=30):
                return .2
            state["areas_manual"] = [(a.type, a.x, a.y, a.width, a.height) for a in window().screen.areas]
            capture("hint-1-first-manual.png")
            where = state["anchor_manual"] = anchored()
            checks["first_hint_stands_under_the_chat_button"] = bool(where.get("under_the_button"))
            checks["first_hint_arrow_points_at_the_button"] = bool(where.get("points_at_it"))
            checks["the_recorded_rect_is_the_drawn_button"] = button_pixels(OUT / "hint-1-first-manual.png", True)
            shown, view = card_rect()
            state["plans"]["manual"] = {"labels": labels(), "text": texts(), "card": shown, "view": view}
            checks["first_manual_shows_the_hint"] = card() is not None and card().hint == "manual"
            checks["it_names_the_shortcut_as_bound"] = ("Ctrl+Option+N" if sys.platform == "darwin" else "Ctrl+Alt+N") in texts()
            checks["it_says_where_the_button_is"] = "next to Manual" in " ".join(texts().split())
            checks["its_button_offers_to_hide"] = labels() == ["Got it", "Hide chat"]
            checks["the_chat_is_still_docked"] = chat_state()[0] == "docked"
            click("toggle")
            state["phase"] = "hidden_by_card"
            return .5
        if phase == "hidden_by_card":
            if not wait(phase, lambda: settled and chat_state()[0] == "hidden" and card() is not None
                        and card().hint == "hidden" and labels() == ["Got it", "Show chat"]):
                return .2
            capture("hint-2-hidden-by-its-button.png")
            where = state["anchor_hidden"] = anchored()
            checks["hidden_hint_stands_under_the_chat_button"] = bool(where.get("under_the_button"))
            checks["hidden_hint_arrow_points_at_the_button"] = bool(where.get("points_at_it"))
            state["plans"]["hidden"] = {"labels": labels(), "text": texts()}
            checks["its_button_hides_the_chat"] = chat_state()[0] == "hidden"
            checks["the_card_becomes_the_hidden_hint"] = card() is not None and "The chat is hidden" in texts()
            checks["its_button_now_offers_to_show"] = labels() == ["Got it", "Show chat"]
            click("toggle")
            state["phase"] = "shown_by_card"
            return .5
        if phase == "shown_by_card":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked" and card() is None):
                return .2
            capture("hint-3-shown-again-labelled-toggle.png")
            checks["its_button_brings_the_chat_back"] = chat_state()[0] == "docked"
            checks["the_card_closes_once_learnt"] = card() is None
            checks["both_hints_are_remembered"] = memory() == {"manual": True, "hidden": True}
            press_shortcut()
            state["phase"] = "no_repeat"
            return .5
        if phase == "no_repeat":
            if card() is not None:
                checks["a_seen_hint_never_shows_again"] = False
            if not hold(phase, 3.0):
                return .2
            checks["the_shortcut_hid_the_chat"] = chat_state()[0] == "hidden"
            checks.setdefault("a_seen_hint_never_shows_again", True)
            capture("hint-4-hidden-again-no-hint.png")
            chat_hint._path().write_text(json.dumps({"manual": True}))   # Only the first hint seen.
            press_shortcut()
            state["phase"] = "close_area_setup"
            return .5
        if phase == "close_area_setup":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked"):
                return .2
            if not hold(phase, 1.5):   # The watch has seen it docked.
                return .2
            area = chat_state()[2]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.screen.area_close()   # Blender's own Close Area: not the toggle.
            state["phase"] = "close_area_hint"
            return .5
        if phase == "close_area_hint":
            if not wait(phase, lambda: settled and card() is not None and labels()):
                return .2
            capture("hint-5-close-area-hidden-hint.png")
            where = state["anchor_close_area"] = anchored()
            checks["close_area_hint_points_at_the_button"] = bool(where.get("points_at_it"))
            checks["close_area_shows_the_hidden_hint"] = card() is not None and card().hint == "hidden"
            from angelo_agent import input_policy
            state["areas_after_close"] = [(a.type, a.x, a.y, a.width, a.height) for a in window().screen.areas]
            state["window_size"] = (window().width, window().height)
            state["at_shortcut"] = {"interactions": input_policy.active_interactions(window()),
                                    "layout_blocking": input_policy.layout_blocking(window())}
            press_shortcut()   # Shown with the hint on screen: a corner card never makes the dock wait.
            state["phase"] = "shown_by_shortcut"
            return .5
        if phase == "shown_by_shortcut":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked" and card() is None):
                return .2
            errors = [m.get("text") for m in ui.get_controller().state["messages"] if m.get("role") == "error"][-3:]
            state["after_shortcut"] = {"state": chat_state()[0], "card": card() is not None, "reports": reports(),
                                       "layout_timer": ui._layout_timer is not None, "chat_errors": errors,
                                       "interactions": __import__("angelo_agent.input_policy", fromlist=["x"]).active_interactions(window())}
            checks["the_shortcut_shows_the_chat_beside_the_hint"] = chat_state()[0] == "docked"
            checks["bringing_it_back_closes_and_remembers"] = card() is None and memory() == {"manual": True, "hidden": True}
            chat_hint._path().write_text(json.dumps({"manual": True}))
            state["phase"] = "close_area_again"
            return .5
        if phase == "close_area_again":
            if not hold(phase, 1.5):   # The watch has seen it docked.
                return .2
            area = chat_state()[2]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.screen.area_close()
            state["phase"] = "got_it"
            return .5
        if phase == "got_it":
            if not wait(phase, lambda: settled and card() is not None and labels()):
                return .2
            click("dismiss")
            state["phase"] = "dismissed"
            return .5
        if phase == "dismissed":
            if not wait(phase, lambda: card() is None):
                return .2
            checks["got_it_dismisses_and_remembers"] = card() is None and memory() == {"manual": True, "hidden": True}
            checks["dismissing_leaves_the_chat_hidden"] = chat_state()[0] == "hidden"
            chat_hint._path().unlink(missing_ok=True)
            director.request_mode(window(), "DIRECTOR")
            state["phase"] = "director_again"
            return .8
        if phase == "director_again":
            if not wait(phase, lambda: settled and director.is_director(window())):
                return .2
            if card() is not None:
                checks["director_never_shows_a_hint"] = False
            if not hold(phase, 3.0):
                return .2
            checks.setdefault("director_never_shows_a_hint", True)
            capture("hint-6-director-no-hint.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
