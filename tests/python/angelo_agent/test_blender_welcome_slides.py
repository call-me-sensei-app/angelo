# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The welcome's feature slideshow in the running app (1.0.5, ``welcome_slides``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference.
Covered, each with screenshots: every slide renders (its picture's pixels differ from the card's ground and from the
other slides' pictures; its headline and pill are the translated ones, whole; the card fits its 3D view) in English,
Arabic, German and Russian at UI scale 1 and in English, German and Arabic at scale 2; Right and Left change slides
(mirrored in Arabic); the arrows and a dot change slides by real clicks; it advances by itself and holds while the
pointer is over it; Escape closes the welcome; hovering a pill shows its tooltip; and each pill, by a real click,
closes the welcome and opens its feature through the shared openers: in Director Media Studio's window and the
Generation Flows rows without changing the layout, the Asset Shelf (when this build has the slide) and the chat; in
Manual the Media Studio and Generation Flows tabs, leaving the tab it came from as it was. And the setup card's Look
row: with Angelo Light chosen (a real click) its note that Angelo's own surfaces stay dark is drawn whole, by plan and
by pixels, in English, German and Arabic at scale 1 and 2, and with Dark chosen it is gone.

Run through ``run_native_suites.py --gui --include test_blender_welcome_slides``; check ``welcome-slides.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))   # qa_frames

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-welcome-slides-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "welcome-slides.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "slides": {}}
checks = state["checks"]
started = time.monotonic()
LOOKS = [("en_US", 1.0), ("ar_EG", 1.0), ("de_DE", 1.0), ("ru_RU", 1.0), ("en_US", 2.0), ("de_DE", 2.0), ("ar_EG", 2.0)]
TRIES = []   # (mode, slide key), filled at boot: every pill in Director, Media Studio and Flows in Manual too.


def window():
    return bpy.context.window_manager.windows[0]


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    """A capture's pixels as an (height, width, 3) array of 0-255 integers, bottom row first."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def numpy_abs_mean(a, b):
    import numpy
    return numpy.abs(a.astype(numpy.int32) - b.astype(numpy.int32)).mean()


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def layout_signature():
    return sorted((a.type, a.width, a.height) for a in window().screen.areas)


def welcome_region():
    from angelo_agent import intro
    area = next((a for a in window().screen.areas if a.as_pointer() == intro._open.get("area")), None)
    return next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None


def to_window(rect):
    """A welcome rectangle (layout pixels) as window pixels, mirrored back in a right-to-left language."""
    from angelo_agent import ui_text
    region = welcome_region()
    x, y, w, h = rect
    left = region.width - x - w if ui_text.is_rtl() else x
    return region.x + left, region.y + y, w, h


def point(action, value=None):
    from angelo_agent import intro
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action and (value is None or v == value))
    x, y, w, h = to_window(rect)
    return x + w / 2, y + h / 2


def press(x, y, target=None):
    w = target or window()
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        w.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def move(x, y):
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def key(kind):
    """A key over the welcome's 3D view (the welcome takes keys only there; the chat beside it keeps its own)."""
    region = welcome_region()
    x, y = (region.x + region.width // 2, region.y + 8) if region else (window().width // 2, window().height // 2)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def away():
    """A point in the 3D view off the card (the scrim): the slideshow is not held there."""
    region = welcome_region()
    return region.x + 6, region.y + region.height // 2


def picture(path):
    """The current slide's picture, cropped from a capture, and the card ground beside it."""
    from angelo_agent import intro
    plan = intro._open["plan"]
    rect = next(op[2] for op in plan["ops"] if op[0] == "slide_picture")
    x, y, w, h = (int(round(v)) for v in to_window(rect))
    px = pixels(path)
    return px[y + 2:y + h - 2, x + 2:x + w - 2]


def look_at_slides(tag):
    """Show each slide (no fade), capture it and check it: drawn, distinct, translated and whole, the card fitting."""
    import numpy
    from angelo_agent import intro, welcome_slides
    from angelo_agent.ui_text import iface_
    crops, report = [], {}
    for index, (key_, title, _, pill, _) in enumerate(welcome_slides.slides()):
        intro.show_slide(index)
        intro._open["slide_changed"] = 0.0   # Fully faded in.
        path = capture("welcome-slides-%s-%d-%s.png" % (tag, index + 1, key_))
        plan = intro._open["plan"]
        crop = picture(path)
        ground = numpy.array([round(c * 255) for c in welcome_slides.welcome_art.FIELD[:3]])
        drawn = float((numpy.abs(crop - ground).sum(axis=2) > 30).mean())
        box = plan["slides"]
        texts = [op[1] for op in plan["ops"] if op[0] == "text" and op[4] == 17 * plan["scale"]
                 and box[1] <= op[3] <= box[1] + box[3]]
        (button,) = [op for op in plan["ops"] if op[0] == "slide_button"]
        whole = "".join(texts).replace(" ", "") == iface_(title).replace(" ", "")
        region = welcome_region()
        fits = plan["height"] <= region.height - 32 * plan["scale"] + 0.5   # The welcome's own rule (layout).
        report[key_] = {"drawn": round(drawn, 3), "headline": texts, "fits": fits, "scale": round(plan["scale"], 3),
                        "height": round(plan["height"]), "view": region.height}
        checks["%s_slide_%d_draws_its_picture" % (tag, index + 1)] = drawn > 0.25
        checks["%s_slide_%d_headline_is_translated_and_whole" % (tag, index + 1)] = bool(texts) and whole and not any(
            t.endswith("…") for t in texts)
        checks["%s_slide_%d_pill_is_its_translated_verb" % (tag, index + 1)] = button[2] == welcome_slides.pill(
            key_, "director")[0]
        checks["%s_slide_%d_card_fits" % (tag, index + 1)] = fits
        crops.append(crop)
    shape = min(c.shape[0] for c in crops), min(c.shape[1] for c in crops)
    distinct = [float(numpy.abs(a[:shape[0], :shape[1]] - b[:shape[0], :shape[1]]).mean())
                for i, a in enumerate(crops) for b in crops[i + 1:]]
    report["pairwise_difference"] = [round(d, 1) for d in distinct]
    checks[tag + "_every_slide_picture_differs"] = min(distinct) > 8
    state["slides"][tag] = report


SETUP_LOOKS = [("en_US", 1.0), ("de_DE", 1.0), ("ar_EG", 1.0), ("en_US", 2.0), ("de_DE", 2.0), ("ar_EG", 2.0)]


def card_region():
    from angelo_agent import card_host
    active = card_host._active
    area = next((a for a in window().screen.areas if a.as_pointer() == active.area), None) if active else None
    return next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None


def click_card(action, value=None):
    """A real click on the visible card's own control (mirrored back in a right-to-left language)."""
    from angelo_agent import canvas_rtl, card_host
    region = card_region()
    rect = next(r for r, a, v in reversed(card_host._active.plan.hits) if a == action and (value is None or v == value))
    press(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width), region.y + rect[1] + rect[3] / 2)


def note_lines():
    """The setup card's Light note as drawn: its text ops (every line of the translated note, in order)."""
    from angelo_agent import card_host, setup_card
    from angelo_agent.ui_text import iface_
    note = iface_(setup_card.LIGHT_NOTE)
    ops = [op for op in card_host._active.plan.ops if op[0] == "text" and op[1].strip() and op[1].strip() in note]
    return note, ops


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_WELCOME_SLIDES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=15):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def open_welcome():
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.intro('INVOKE_DEFAULT')


def flows_shown():
    """The Generation Flows rows are on screen: the chat's + menu opened on them, a Flows menu or popup, or Manual's
    Generation Flows tab (whichever this build's ``angelo.show_flows`` routes to)."""
    from angelo_agent import chat_view, console
    for area in window().screen.areas:
        view = chat_view._views.get(area.as_pointer(), {}) if console.is_chat(area) else {}
        if (view.get("popover") or {}).get("reveal") == "flows":
            return True
    try:
        from angelo_agent import flow_menu
        if flow_menu.is_open(window()):   # Part A's popup under the top bar's Flows button.
            return True
    except ImportError:
        pass
    workspace = window().workspace.name if window().workspace else ""
    return "Flow" in workspace


def tick():
    from angelo_agent import director, intro, studio_toggle, welcome_slides
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 280, "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: settled() and director.is_director(window()), 30):
                return .3
            state["todo"] = list(LOOKS)
            state["setup_todo"] = list(SETUP_LOOKS)
            keys = welcome_slides.keys()
            state["slide_keys"] = keys
            TRIES.extend([("DIRECTOR", k) for k in keys if k != "chat"] + [("DIRECTOR", "chat")]
                         + [("MANUAL", k) for k in ("studio", "flows", "assets") if k in keys])
            state["phase"] = "setup"
            return .3
        if phase == "setup":   # The setup card's Look row: Light says Angelo's own surfaces stay dark (1.0.5, F1).
            from angelo_agent import card_host, splash_setup
            if not state["setup_todo"]:
                prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
                state["phase"] = "look"
                return 1.0
            language, scale = state["setup_todo"][0]
            if prefs.view.language != language or prefs.view.ui_scale != scale:
                prefs.view.language, prefs.view.use_translate_interface, prefs.view.ui_scale = language, True, scale
                return 1.0
            if not card_host.is_active("setup"):
                with bpy.context.temp_override(window=window()):
                    splash_setup.open_setup(window(), False)
                return .6
            if card_host._active.plan is None:
                return .2
            click_card("look", "light")
            state["phase"] = "setup_light"
            return .6
        if phase == "setup_light":
            language, scale = state["setup_todo"][0]
            tag = "%s-x%g" % (language.split("_")[0], scale)
            note, ops = note_lines()
            state.setdefault("setup_note", {})[tag] = [op[1] for op in ops]
            words = " ".join(op[1] for op in ops).split()
            whole = words == note.split() or sorted(words) == sorted(note.split())   # Arabic rows: visual order.
            checks["setup_light_shows_the_note_whole_" + tag] = bool(ops) and whole and not any(
                op[1].endswith("\u2026") for op in ops)
            state["note_ops"] = [list(op[2:5]) for op in ops]
            state["light_capture"] = str(capture("welcome-setup-light-%s.png" % tag))
            click_card("look", "dark")
            state["phase"] = "setup_dark"
            return .6
        if phase == "setup_dark":
            from angelo_agent import card_host, ui_text
            language, scale = state["setup_todo"][0]
            tag = "%s-x%g" % (language.split("_")[0], scale)
            _, ops = note_lines()
            dark = capture("welcome-setup-dark-%s.png" % tag)
            checks["setup_dark_has_no_note_" + tag] = not ops
            # By pixels: the band the note was drawn in under Light reads differently under Dark (the card's own
            # colours are the same in both looks, so the difference is the note).
            region, (cx, _, cw, _) = card_region(), card_host._active.plan.card
            if state["note_ops"]:
                size = state["note_ops"][0][2]
                left = min(op[0] for op in state["note_ops"])
                bottom = min(op[1] for op in state["note_ops"]) - 0.4 * size
                top = max(op[1] for op in state["note_ops"]) + size
                width = cx + cw - left
                if ui_text.is_rtl():
                    left = region.width - left - width
                x0, y0 = int(region.x + left), int(region.y + bottom)
                x1, y1 = int(x0 + width), int(region.y + top)
                change = float(numpy_abs_mean(pixels(state["light_capture"])[y0:y1, x0:x1], pixels(dark)[y0:y1, x0:x1]))
                state.setdefault("setup_note_change", {})[tag] = round(change, 1)
                checks["setup_light_note_is_on_screen_" + tag] = change > 6
            card_host.close()
            state["setup_todo"].pop(0)
            state["phase"] = "setup"
            return .5
        if phase == "look":
            language, scale = state["todo"][0]
            if prefs.view.language != language or prefs.view.ui_scale != scale:
                prefs.view.language, prefs.view.use_translate_interface, prefs.view.ui_scale = language, True, scale
                return 1.0
            if not intro.is_open():
                open_welcome()
                return .6
            if "plan" not in intro._open:
                return .2
            tag = "%s-x%g" % (language.split("_")[0], scale)
            look_at_slides(tag)
            steps = []
            if language == "ar_EG":   # Right goes to the slide on the right: the previous one in Arabic.
                steps.append((lambda: (intro.show_slide(1), key('RIGHT_ARROW')),
                              lambda: intro._open.get("slide") == 0, "ar_right_arrow_key_goes_to_the_slide_on_the_right"))
            steps.append((lambda: key('ESC'), lambda: not intro.is_open(), tag + "_escape_closes_the_welcome"))
            state["steps"], state["after_steps"] = steps, "look_next"
            state["phase"] = "steps"
            return .1
        if phase == "look_next":
            state["todo"].pop(0)
            if not state["todo"]:
                prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
                state["phase"] = "controls"
                return 1.0
            state["phase"] = "look"
            return .3
        if phase == "steps":   # One simulated action per tick; its check on the next (events queue, then run).
            if state.get("pending"):
                check, name = state.pop("pending")
                checks[name] = bool(check())
                state.setdefault("step_report", {})[name] = {"slide": intro._open.get("slide"),
                                                             "open": intro.is_open()}
            if not state["steps"]:
                state["phase"] = state["after_steps"]
                return .2
            action, check, name = state["steps"].pop(0)
            action()
            state["pending"] = (check, name)
            return .5
        if phase == "controls":
            if not intro.is_open():
                open_welcome()
                return .6
            if "plan" not in intro._open:
                return .2
            intro.show_slide(0)
            slide = lambda: intro._open.get("slide")   # noqa: E731
            last = len(welcome_slides.slides()) - 1

            def held_start():
                slides = to_window(intro._open["plan"]["slides"])
                move(slides[0] + 20, slides[1] + 20)

            def stale():   # As if this slide had been up for its whole dwell.
                intro._open["slide_since"] = time.monotonic() - welcome_slides.DWELL - 0.1
            state["steps"] = [   # By itself first: any navigation stops it for good.
                (lambda: (move(*away()), intro.show_slide(0)), lambda: not intro._open.get("over_slides"),
                 "the_pointer_off_the_slideshow_lets_it_run"),
                (stale, lambda: slide() == 1, "it_advances_by_itself"),
                (held_start, lambda: bool(intro._open.get("over_slides")), "the_pointer_over_it_is_seen"),
                (stale, lambda: slide() == 1, "it_holds_while_the_pointer_is_over_it"),
                (lambda: (capture("welcome-slides-hover-held.png"), move(*away()), intro.show_slide(last)),
                 lambda: slide() == last, "shown_the_last_slide"),
                (stale, lambda: slide() == last, "it_rests_on_the_last_slide"),
                (lambda: key('RIGHT_ARROW'), lambda: slide() == 0, "right_arrow_key_goes_to_the_next_slide"),
                (lambda: key('LEFT_ARROW'), lambda: slide() == last, "left_arrow_key_goes_back"),
                (lambda: press(*point("slide_next")), lambda: slide() == 0, "the_next_arrow_goes_to_the_next_slide"),
                (lambda: press(*point("slide_prev")), lambda: slide() == last, "the_previous_arrow_goes_back"),
                (lambda: press(*point("slide_dot", 0)), lambda: slide() == 0 and intro.is_open(),
                 "a_dot_goes_to_its_slide"),
                (lambda: (move(*away()), stale()), lambda: slide() == 0 and intro._open.get("slides_stopped"),
                 "it_stops_for_good_after_the_person_navigates"),
                (lambda: key('ESC'), lambda: not intro.is_open(), "escape_closes_it_after_browsing"),
            ]
            state["after_steps"] = "try"
            state["phase"] = "steps"
            return .1
        if phase == "try":
            if not TRIES:
                state["phase"] = "complete"
                finish()
                return None
            mode, which = TRIES[0]
            if not wait("mode-" + mode, lambda: settled(), 20):
                return .3
            if (mode == "MANUAL") == director.is_director(window()):
                director.request_mode(window(), mode, persist=False)
                return 1.0
            if not intro.is_open():
                state["layout"] = layout_signature()
                state["workspace"] = window().workspace.name
                open_welcome()
                return .6
            if "plan" not in intro._open:
                return .2
            if which == "chat":   # Proof it moves the keyboard to the chat: nothing has it before the click.
                from angelo_agent import chat_view
                for view in chat_view._views.values():
                    view["focused"] = False
                    view.pop("focus_request", None)
            index = welcome_slides.keys().index(which)
            if intro._open["plan"].get("slide") != index:   # Its pill exists once the welcome has drawn the slide.
                intro.show_slide(index)
                intro._open["slide_changed"] = 0.0
                return .4
            tag = "%s-%s" % (mode.lower(), which)
            (button,) = [op for op in intro._open["plan"]["ops"] if op[0] == "slide_button"]
            here = "standard" if mode == "MANUAL" else "director"
            checks["the_%s_pill_reads_its_%s_label" % (which, mode.lower())] = button[2] == welcome_slides.pill(
                which, here)[0]
            if which == "studio" and mode == "DIRECTOR":   # The pill's hover state and tooltip, once.
                move(*point("slide_try", which))
                state["phase"] = "hovered"
                return .5
            state["card"] = [int(round(v)) for v in to_window(intro._open["plan"]["card"])]
            state["before"] = str(capture("welcome-slides-pill-%s-before.png" % tag))
            press(*point("slide_try", which))
            state["phase"] = "tried"
            return .5
        if phase == "hovered":
            tips = [op for op in intro._open["plan"]["ops"] if op[0] == "slide_tip"]
            capture("welcome-slides-pill-hover-tooltip.png")
            checks["hovering_a_pill_shows_its_tooltip"] = bool(tips) and tips[-1][2] == welcome_slides.tooltip("studio")
            state["card"] = [int(round(v)) for v in to_window(intro._open["plan"]["card"])]
            state["before"] = str(capture("welcome-slides-pill-director-studio-before.png"))
            press(*point("slide_try", "studio"))
            state["phase"] = "tried"
            return .5
        if phase == "tried":
            from angelo_agent import chat_toggle, studio_workspaces
            mode, which = TRIES[0]
            windows = bpy.context.window_manager.windows
            manual = mode == "MANUAL"
            opened = {"studio": lambda: (studio_workspaces.active(window(), "studio") if manual
                                         else studio_toggle.opened_window(windows) is not None),
                      "assets": lambda: any(a.type == 'VIEW_3D' and a.spaces.active.show_region_asset_shelf
                                            for a in window().screen.areas),
                      "flows": lambda: studio_workspaces.active(window(), "flows") if manual else flows_shown(),
                      "chat": lambda: chat_toggle.chat_state(window(), windows)[0] != "hidden"}
            if not wait("tried-%s-%s" % (mode, which), lambda: not intro.is_open() and opened[which](), 8):
                return .2
            tag = "%s-%s" % (mode.lower(), which)
            new = [w for w in windows if w.as_pointer() != window().as_pointer()]
            # On screen, not only in state: the main window's card area no longer shows the welcome. Captured after a
            # real redraw (a workspace switch applies on the next event-loop pass), retried for up to 3 s.
            shown_since = state.setdefault("since", {}).setdefault("shown-%s-%s" % (mode, which), time.monotonic())
            from angelo_agent import studio
            draws_at_open = state.setdefault("studio_draws_at_open", {}).setdefault(tag, studio.draw_count)
            if time.monotonic() - shown_since < .6:
                return .2
            if new and which == "studio" and time.monotonic() - shown_since < 6 and (
                    time.monotonic() - shown_since < 1.5 or studio.draw_count <= draws_at_open):
                return .2   # Studio's own window: captured once it has been open 1.5 s and Studio drew in it since.
            main = capture("welcome-slides-pill-%s-main-after.png" % tag)
            x, y, w, h = state["card"]
            change = float(numpy_abs_mean(pixels(main)[y:y + h, x:x + w], pixels(state["before"])[y:y + h, x:x + w]))
            state.setdefault("after_change", {})[tag] = round(change, 1)
            if change < 12 and time.monotonic() - shown_since < 3.6:
                return .3
            if new and which == "studio":   # Studio's own window shows Studio, never a flat frame (35f5b544698).
                from qa_frames import content_share
                share = content_share(pixels(capture("welcome-slides-pill-%s-after.png" % tag, new[-1])))
                state.setdefault("studio_window_content", {}).setdefault(tag, []).append(round(share, 4))
                retries = len(state["studio_window_content"][tag]) - 1
                if share <= 0.02 and retries < 3:
                    return .5   # A flat capture is taken again, 0.5 s apart, before the check fails (a capture race).
                checks["the_%s_pill_window_draws_its_content_in_%s" % (which, mode.lower())] = share > 0.02
            checks["the_%s_pill_closes_the_welcome_and_opens_it_in_%s" % (which, mode.lower())] = True
            checks["the_%s_pill_result_is_on_screen_in_%s" % (which, mode.lower())] = change >= 12
            if manual and which in ("studio", "flows"):
                checks["the_%s_pill_shows_its_tab_in_manual" % which] = window().workspace.name == {
                    "studio": "Media Studio", "flows": "Generation Flows"}[which]
            if not manual and which in ("studio", "flows"):
                checks["the_%s_pill_keeps_directors_layout" % which] = layout_signature() == state["layout"]
            for w in new:   # Close what it opened, so the next pill starts from the same screen.
                with bpy.context.temp_override(window=w, area=w.screen.areas[0]):
                    bpy.ops.wm.window_close()
            if manual:   # Back to the tab it came from: it is as it was (switching tabs is navigation).
                window().workspace = bpy.data.workspaces[state["workspace"]]
                state["phase"] = "returned"
                return .8
            if which == "flows":
                try:
                    from angelo_agent import flow_menu
                    flow_menu.close()
                except ImportError:
                    key('ESC')
            TRIES.pop(0)
            state["phase"] = "try"
            return .6
        if phase == "returned":
            mode, which = TRIES.pop(0)
            checks["the_%s_pill_leaves_the_manual_tab_it_left_as_it_was" % which] = layout_signature() == state["layout"]
            state["phase"] = "try"
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
