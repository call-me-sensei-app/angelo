# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat draws, hit-tests and is clicked in one place at every interface scale, left to right and right to left,
and ``qa_hit`` is the mapping that finds that place (2.1.0).

On 2026-11-15 a suite running under a 100 DPI display (interface scale 1.0417) clicked beside the + menu's rows: the
menu drew where it hit-tested, but the suite's hand-made mapping ignored the scale. Every suite now maps through
``qa_hit`` (``test_native_qa_hit`` refuses the hand form); this suite proves ``qa_hit`` against the running app.

For each pass (Resolution Scale set so the chat's scale is 1.0, 1.5 and 2.0 in English, and 1.5 in Arabic, which
mirrors the canvas), in Director Mode at the default startup:

* the chat's scale is the one asked for (``chat_view.ui_scale``);
* a real click (``Window.event_simulate``) where ``qa_hit`` puts the + button opens the + menu at its top level;
* drawn == hit: the menu is drawn inside a scissor of its own rect, so the pixels that change between the window
  without it and with it, along the menu's middle column and middle row, end where ``qa_hit.rect`` puts its
  ``popup_rect`` (2 screen pixels at most, the scissor's rounding);
* a real click where ``qa_hit`` puts the menu's first group tile opens that group's page.

Run through ``run_native_suites.py --gui --include test_blender_chat_hit_scale``; check ``chat-hit-scale.json`` for
``success`` and ``phase == "complete"``. Under Xvfb, start the display at ``-dpi 96`` (the passes set the scale from
whatever the display adds, so another DPI still passes, but every other suite assumes 96).
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

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_hit  # noqa: E402  (the mapping under test)
import qa_input  # noqa: E402  (a click as a person makes it)
import qa_plus_menu  # noqa: E402  (the + menu's state)

product_env.clear("DISABLE_DEFAULT_CHAT")
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-hit-scale"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "chat-hit-scale.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
PASSES = ((1.0, "en_US"), (1.5, "en_US"), (2.0, "en_US"), (1.5, "ar_EG"))
EDGE = 2.0      # Screen pixels an edge may sit from qa_hit's: the scissor rounds each edge to a whole pixel.
MARGIN = 4.0    # Window pixels scanned past each edge: the menu draws nothing outside its scissor.
DIFFERS = 0.03  # Summed RGB (0..1) that counts as a changed pixel; the menu's border is far brighter than that.
state = {"phase": "boot", "success": False, "checks": {}, "passes": [], "queue": list(PASSES), "screenshots": []}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return area, (next(r for r in area.regions if r.type == 'WINDOW') if area else None)


def view():
    from angelo_agent import chat_view
    area, _ = chat()
    return chat_view._views.get(area.as_pointer(), {}) if area else {}


def capture(name):
    path = OUT / ("%s.png" % name)
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    """The capture as an (rows, columns, 3) array, row 0 at the bottom as window y is."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return values.reshape(height, width, 4)[..., :3]


def drawn_edges(closed, opened, expected):
    """Where the pixels that changed when the menu opened end, in screen pixels: (left, bottom, right, top) along the
    middle row and column of ``expected`` (window pixels), or None when nothing changed there."""
    import numpy
    k = opened.shape[1] / float(window().width)   # Screen pixels per window pixel (2 on a Retina display).
    x, y, w, h = (v * k for v in expected)
    pad = MARGIN * k
    changed = numpy.abs(opened - closed).sum(axis=2) > DIFFERS
    column, row = int(x + w / 2), int(y + h / 2)
    lo_y, hi_y = max(0, int(y - pad)), min(changed.shape[0], int(y + h + pad) + 1)
    lo_x, hi_x = max(0, int(x - pad)), min(changed.shape[1], int(x + w + pad) + 1)
    rows = numpy.nonzero(changed[lo_y:hi_y, column])[0]
    columns = numpy.nonzero(changed[row, lo_x:hi_x])[0]
    if not len(rows) or not len(columns):
        return None
    return (lo_x + float(columns.min()), lo_y + float(rows.min()), lo_x + float(columns.max()) + 1,
            lo_y + float(rows.max()) + 1), (x, y, x + w, y + h), k


def language(locale):
    available = {item.identifier for item in prefs.view.bl_rna.properties["language"].enum_items}
    if locale not in available:
        raise AssertionError("this build offers no %s interface" % locale)
    prefs.view.language, prefs.view.use_translate_interface = locale, True


def close_menu():
    view().pop("popover", None)
    for area in window().screen.areas:
        area.tag_redraw()


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CHAT_HIT_SCALE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    prefs.view.language, prefs.view.ui_scale = 'en_US', 1.0
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import chat_view, director
    try:
        assert time.monotonic() - started < 280, "Suite timed out in " + state["phase"]
        phase = state["phase"]
        current = state["passes"][-1] if state["passes"] else None
        if phase == "boot":
            if not (qa_boot.started() and chat()[0] is not None and view().get("hits")):
                return .3
            checks["starts_in_director_mode"] = director.is_director(window())
            # What the display adds to Resolution Scale (pixel size, DPI): each pass asks for the chat's own scale.
            state["display_factor"] = chat_view.ui_scale() / prefs.view.ui_scale
            state["phase"] = "next"
            return .1
        if phase == "next":
            if not state["queue"]:
                state["phase"] = "complete"
                return .1
            scale, locale = state["queue"].pop(0)
            tag = "%s-x%g" % (locale, scale)
            state["passes"].append({"tag": tag, "scale": scale, "locale": locale})
            close_menu()
            language(locale)
            prefs.view.ui_scale = scale / state["display_factor"]
            for area in window().screen.areas:
                area.tag_redraw()
            state.update(phase="hover", at=time.monotonic())
            return .8
        tag = current["tag"]
        _, region = chat()
        if phase == "hover":   # The pointer over +, so the capture without the menu has the same hover.
            plus = next((r for r, a, _ in view().get("hits", []) if a == "attach"), None)
            if plus is None:
                assert time.monotonic() - state["at"] < 10, "no + button drawn"
                return .3
            current["chat_scale"] = round(chat_view.ui_scale(), 4)
            checks["the_chat_is_at_the_scale_asked_" + tag] = abs(chat_view.ui_scale() - current["scale"]) < 0.01
            x, y = qa_hit.ipoint(qa_hit.LAYOUT, region, plus)
            current["plus"] = [x, y]
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            state["phase"] = "closed"
            return .5
        if phase == "closed":
            state["closed"] = capture("%s-closed" % tag)
            qa_input.press(window(), *current["plus"])
            state.update(phase="open", at=time.monotonic())
            return .5
        if phase == "open":
            if not qa_plus_menu.at_top(view()):
                if time.monotonic() - state["at"] > 10:
                    checks["a_click_where_qa_hit_puts_plus_opens_the_menu_" + tag] = False
                    state["phase"] = "next"
                return .3
            checks["a_click_where_qa_hit_puts_plus_opens_the_menu_" + tag] = True
            rect = view()["popup_rect"]
            expected = qa_hit.rect(qa_hit.LAYOUT, region, rect)
            found = drawn_edges(pixels(state["closed"]), pixels(capture("%s-open" % tag)), expected)
            current["popup_rect"], current["expected_window"] = list(rect), [round(v, 1) for v in expected]
            if found:
                drawn, wanted, k = found
                current["drawn_screen"], current["expected_screen"] = list(drawn), [round(v, 1) for v in wanted]
                current["edge_gaps"] = [round(abs(a - b), 1) for a, b in zip(drawn, wanted)]
            checks["the_menu_is_drawn_where_qa_hit_puts_it_" + tag] = bool(found) and all(
                gap <= EDGE * found[2] for gap in current["edge_gaps"])
            tile = next(((r, v) for r, a, v in qa_plus_menu.hits(view()) if a == qa_plus_menu.GROUP
                         and qa_plus_menu.inside(r, rect)), None)
            assert tile is not None, "the menu shows no group tile"
            current["group"] = tile[1]
            qa_input.press(window(), *qa_hit.point(qa_hit.LAYOUT, region, tile[0]))
            state.update(phase="page", at=time.monotonic())
            return .5
        if phase == "page":
            opened = qa_plus_menu.on_page(view(), current["group"])
            if not opened and time.monotonic() - state["at"] < 5:
                return .3
            current["page"] = qa_plus_menu.page(view())
            checks["a_click_where_qa_hit_puts_a_tile_opens_its_page_" + tag] = opened
            state["phase"] = "next"
            return .1
        if phase == "complete":
            close_menu()
            finish()
            return None
        raise AssertionError("unknown phase " + phase)
    except Exception as error:  # Report and quit: a GUI suite never hangs.
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=0.5, persistent=True)
