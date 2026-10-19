# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The shared UI kit (``ui_kit*``) in the real window: every component in every state, in a card_host card.

A gallery card (defined here, shown through the real ``card_host``) draws each component with the card_kit
Builder. Real mouse and keyboard events (``Window.event_simulate``) drive it: hover, press, keyboard focus,
disabled, toggle flips, segmented selection, the notice's Details disclosure, Copy, text-field typing with
masking and reveal, and dropdown selection. Screenshots in English, Arabic (right to left) and a narrow card.
Nothing leaves the computer: no provider, account, network or inference.

Run through ``run_native_suites.py --gui --include test_blender_ui_kit_gallery``; check ``ui-kit-gallery.json``
for ``success`` and ``phase == "complete"``. Look at the screenshots it lists.
"""

import json
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_crash_gui as gui  # noqa: E402  (window, pointer and screenshot helpers)
import ui_kit_gallery_card as gallery  # noqa: E402
from update_crash_gui import until, window  # noqa: E402

bpy.context.preferences.use_preferences_save = False
gui.OUT.mkdir(parents=True, exist_ok=True)
REPORT = gui.OUT / "ui-kit-gallery.json"
state = {"phase": "early", "success": False, "checks": {}, "screenshots": gui.shots,
         "fixture": "A gallery card in the real card_host; synthetic input; no network, account or inference"}
started = time.monotonic()


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
        state["failed_checks"] = [name for name, value in state["checks"].items() if not value]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_UI_KIT_GALLERY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "The fixture timed out in " + state["phase"]
        if state["phase"] == "early":
            state["phase"] = "run"
            state["scenario"] = scenario(state["checks"])
            return .5
        try:
            next(state["scenario"])
        except StopIteration:
            finish()
            return None
        return .15
    except Exception as error:
        finish(error)
        return None


def ui():
    return gui.host()._active.ui


def hover_is(action, value=None):
    return ui().hover == (action, value)


def scenario(checks):
    from angelo_agent import card_host
    yield from until("a 3D view", lambda: any(a.type == 'VIEW_3D' for a in window().screen.areas))
    for _ in range(2):   # Close the startup splash (Escape, then the pointer away from it).
        window().event_simulate(type='ESC', value='PRESS', x=40, y=40)
        window().event_simulate(type='ESC', value='RELEASE', x=40, y=40)
        window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=20, y=20)
        yield
    if not hasattr(bpy.types, "ANGELO_OT_card_host"):   # app_notices registers the host; suites run without notices.
        card_host.register()
    from angelo_agent import chat_view
    if chat_view._shader is None:   # Without the default chat its primitives were never bound (card_host draws with them).
        import gpu
        chat_view.reload_fonts()
        chat_view._shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    card = gallery.Gallery()
    card_host.request(card)
    yield from until("the gallery card", lambda: card_host.pump(lambda: True) or gui.active(card.kind))
    yield from until("its first layout", lambda: gui.active(card.kind))
    state["window"] = [window().width, window().height]
    yield from scene_status(checks, card)
    yield from scene_controls(checks, card)
    yield from scene_fields(checks, card)
    yield from scene_rows(checks, card)
    yield from scene_narrow_and_arabic(checks, card)
    card_host.close("done")


def show_page(card, page):
    card.page = page
    card.ver += 1
    gui.host()._redraw()


def scene_status(checks, card):
    show_page(card, 0)
    gui.park_pointer()
    yield
    gui.capture("ui-kit-01-status-rest.png")
    ops = gui.host()._active.plan.ops
    kits = [op for op in ops if op[0] == "kit"]
    checks["status_page_draws_every_chip"] = sorted(op[3]["kind"] for op in kits if op[1] == "chip") == sorted(
        ["running", "done", "failed", "needs_you", "preview", "paid"])
    checks["status_page_draws_four_notices"] = sorted(op[3]["kind"] for op in kits if op[1] == "notice") == sorted(
        ["info", "warn", "error", "success"])
    destructive = [op for op in ops if op[0] == "button" and op[3] == "destructive"]
    checks["destructive_buttons_enabled_and_disabled"] = sorted(op[4][3] for op in destructive) == [False, True]
    gui.move_to(*gui.center(gui.hit_rect("retry")))
    yield
    checks["notice_action_hover"] = hover_is("retry")
    gui.capture("ui-kit-02-status-hover.png")
    x, y = gui.center(gui.hit_rect("delete"))
    gui.move_to(x, y)
    gui.press_at(x, y)
    yield
    checks["destructive_pressed"] = ui().pressed == ("delete", None) and hover_is("delete")
    gui.capture("ui-kit-03-status-destructive-pressed.png")
    gui.release_at(x, y)
    yield
    checks["destructive_click_acts"] = card.log[-1:] == [("delete", None)]
    gui.park_pointer()
    gui.key('TAB')
    yield
    checks["tab_focuses_first_control"] = ui().focus == gui.host()._active.plan.focus_order[0]
    gui.capture("ui-kit-04-status-focus.png")
    gui.click("details")
    yield
    checks["details_disclosure_expands"] = card.expanded
    yield
    gui.capture("ui-kit-05-status-details-open.png")
    gui.click("details")
    yield
    checks["details_disclosure_collapses"] = not card.expanded


def scene_controls(checks, card):
    show_page(card, 1)
    gui.park_pointer()
    yield
    gui.capture("ui-kit-06-controls-rest.png")
    before = card.on["sync"]
    gui.click("toggle", "sync")
    yield
    checks["toggle_flips"] = card.on["sync"] is (not before)
    gui.click("toggle", "sync")
    yield
    checks["toggle_flips_back"] = card.on["sync"] is before
    checks["disabled_toggle_has_no_hit"] = not any(a == "toggle" and v == "locked" for _, a, v in gui.host()._active.plan.hits)
    gui.click("theme", "light")
    yield
    checks["segmented_selects"] = card.theme == "light"
    gui.click("swatch", "amber")
    yield
    checks["segmented_swatch_selects"] = card.swatch == "amber"
    gui.move_to(*gui.center(gui.hit_rect("theme", "dark")))
    yield
    checks["segment_hover"] = hover_is("theme", "dark")
    gui.capture("ui-kit-07-controls-selected-hover.png")
    gui.click("copy", "script")
    yield
    checks["copy_marks_copied"] = card.copied == "script"
    yield
    gui.park_pointer()
    gui.capture("ui-kit-08-controls-copied.png")


def scene_fields(checks, card):
    if not gallery.HAS_FIELDS:
        state["fields"] = "Text field and dropdown not in this build of the kit yet"
        return
    yield from gallery.drive_fields(checks, card, state)


def scene_rows(checks, card):
    if not gallery.HAS_ROWS:
        state["rows"] = "List rows and provider tiles not in this build of the kit yet"
        return
    yield from gallery.drive_rows(checks, card, state)


def scene_narrow_and_arabic(checks, card):
    card.narrow = True
    for page in range(gallery.PAGES):
        show_page(card, page)
        gui.park_pointer()
        yield
        gui.capture("ui-kit-20-narrow-page%d.png" % page)
        plan = gui.host()._active.plan
        checks["narrow_page%d_controls_inside_card" % page] = all(gallery.inside(plan.card, rect) for rect, _, _ in plan.hits)
    card.narrow = False
    prefs_view = bpy.context.preferences.view
    previous, prefs_view.ui_scale = prefs_view.ui_scale, 1.5   # Every size follows the interface scale.
    for page in (0, 2):
        show_page(card, page)
        gui.park_pointer()
        yield
        yield
        gui.capture("ui-kit-25-scale150-page%d.png" % page)
        plan = gui.host()._active.plan
        checks["scale150_page%d_controls_inside_card" % page] = plan.scale > 1.0 and all(
            gallery.inside(plan.card, rect) for rect, _, _ in plan.hits)
    prefs_view.ui_scale = previous
    if not bpy.app.build_options.international:
        state["arabic"] = "WITH_INTERNATIONAL is off in this build: the Arabic layout was not checked"
        return
    prefs = bpy.context.preferences.view
    prefs.use_translate_interface = True
    prefs.language = 'ar_EG'
    yield
    yield
    from angelo_agent.ui_text import is_rtl
    checks["arabic_is_right_to_left"] = is_rtl()
    for page in range(gallery.PAGES):
        show_page(card, page)
        gui.park_pointer()
        yield
        gui.capture("ui-kit-30-arabic-page%d.png" % page)
    prefs.language = 'en_US'
    yield


if __name__ == "__main__":
    bpy.app.timers.register(tick, first_interval=1.0)
