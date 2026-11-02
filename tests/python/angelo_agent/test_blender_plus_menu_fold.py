# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat bar's + menu shows every feature without scrolling, drawn in a windowed Angelo in Director Mode, with the
app's own fonts and catalogues (doc/angelo/agent_chat.rst, "The + menu"; 1.0.8).

1.0.8 regression this pins: at a 1440 x 900 screen the menu showed 10 of its 19 features, 6 once a group had been
opened. Real default startup and real input (``Window.event_simulate``). Two kinds of check:

* Drawn, at the window each run is started with (the manifest's variants: 1440 x 875 and 1280 x 775, a 1440 x 900
  and a 1280 x 800 screen less the menu bar; the run fails if the window is not that size), in English, German,
  Japanese and Swedish (Swedish has the least room to spare): a click on + opens the menu at its top level, and every
  feature in ``chat_destinations.TOP_LEVEL`` has a hit wholly inside the menu with nothing scrolled
  (``qa_plus_menu.fold``), each captured.
* Every group's page (Base meshes, Generation Flows, …) at the top, middle and end of its list: no text crosses from
  the list into the page's header, its pinned toggles, the strip or the footer (qa_plus_menu.crossings: the text's
  visible part, clipped as drawn), captured for the base meshes.
* The keyboard, in English: Tab reaches every feature, Return on Generation Flows opens its page, Esc goes back and
  Esc again closes the menu.
* Laid out at the common laptop sizes, in every language: the window cannot be resized from Python, so the top level
  is laid out by the draw's own code (``chat_add_draw.top_level``: the canvas's fonts, the current language's catalogues) for the
  chat of a 1440 x 875 and a 1280 x 775 window (a 1440 x 900 and a 1280 x 800 screen less the menu bar), sized from
  this window's own layout (the chat's share of the width, the rows above and below it); in all 21 languages: every
  feature placed in view, nothing scrolled, the menu inside the chat, every title whole and within its tile.

Run through ``run_native_suites.py --gui --include test_blender_plus_menu_fold`` (both variants: ``@w1440``,
``@w1280``); check each run's ``plus-menu-fold.json`` for ``success`` and ``phase == "complete"``.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/angelo"))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (synthetic clicks)
import qa_plus_menu  # noqa: E402  (the + menu's fold, read from the chat's canvas state)
from i18n import languages  # noqa: E402

product_env.clear("DISABLE_DEFAULT_CHAT")
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-plus-menu-fold"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "plus-menu-fold.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
DRAWN = ("en_US", "de_DE", "ja_JP", "sv_SE")
# The window this run was started at (the manifest's variants: ANGELO_PLUS_MENU_WINDOW = "1440x875"), else None.
REQUESTED = tuple(int(v) for v in os.environ["ANGELO_PLUS_MENU_WINDOW"].split("x")) \
    if os.environ.get("ANGELO_PLUS_MENU_WINDOW") else None
TAG = "%dx%d" % REQUESTED if REQUESTED else "default"
LAID_OUT = ("en_US",) + tuple(language.locale for language in languages.LANGUAGES)
WINDOWS = ((1440, 875), (1280, 775))
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "drawn": {}, "laid_out": {},
         "queue": list(DRAWN)}
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


def hit_point(action, value=None):
    _, region = chat()
    rect = next((r for r, a, v in view().get("popup_hits", []) + view().get("hits", [])
                 if a == action and (value is None or v == value)), None)
    return None if rect is None else (region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2)


def key(kind):
    """A key press over the chat, as a person's keyboard sends it while the menu is open."""
    x, y = (int(v) for v in hit_point("attach"))   # Over the open menu, above the composer's + (as feedback_gui).
    y += 80
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def language(locale):
    available = {item.identifier for item in prefs.view.bl_rna.properties["language"].enum_items}
    if locale not in available:
        raise AssertionError("this build offers no %s interface" % locale)
    prefs.view.language, prefs.view.use_translate_interface = locale, True


def chat_sizes():
    """The chat's (width, height) in a window of each size in ``WINDOWS``, from this window's own layout: the chat's
    share of the window's width, and the window's height less what sits above and below the chat."""
    from angelo_agent import chat_view
    w = window()
    _, region = chat()
    pixels, scale = prefs.system.pixel_size, chat_view.ui_scale()   # Window sizes are points; the canvas lays out
    share, rest = region.width / w.width, w.height - region.height  # region pixels / its scale.
    state["layout_basis"] = {"window": [w.width, w.height], "chat": [region.width, region.height],
                             "share": round(share, 4), "rest": rest, "pixel_size": pixels, "ui_scale": scale}
    return {"%dx%d" % size: (int(size[0] * pixels * share / scale), int((size[1] * pixels - rest) / scale))
            for size in WINDOWS}


def reading(lines):
    """Drawn lines as the text they read, without spaces or word marks (Arabic lines are visual, each on its own)."""
    from angelo_agent import rtl_text
    text = "".join(rtl_text.to_logical(line) if rtl_text.is_visual(line) else line for line in lines)
    return "".join(text.split()).replace("\u200b", "")


def laid_out(locale):
    """Every feature placed in view, nothing scrolled, every title whole in its tile, at each window size."""
    from angelo_agent import chat_add_draw, chat_add_layout as layout, chat_destinations, chat_view
    bottom = view().get("add_bottom", 120)
    found = {}
    for name, (width, height) in chat_sizes().items():
        _, items, geometry = chat_add_draw.top_level(chat_view, width, height, bottom)
        placed = {items[cell["index"]]["feature"]: layout.in_view(cell["rect"], geometry["list_rect"])
                  for cell in geometry["cells"]}
        placed.update({items[i]["feature"]: layout.in_view(rect, geometry["rect"])
                       for i, rect in geometry["attach"] + geometry["links"]})
        cut = []
        for cell in geometry["cells"]:
            item, width_of = items[cell["index"]], cell["rect"][2]
            room = width_of - layout.TITLE_X - (layout.CHEVRON if item["kind"] == "group" else layout.END)
            whole = reading(cell["lines"]) == reading([item["title"]])
            if not whole or any(chat_view.measure(line, layout.TITLE) > room + 0.5 for line in cell["lines"]):
                cut.append(item["feature"])
        popup = geometry["rect"]
        found[name] = {"chat": [width, height], "popup": [round(v, 1) for v in popup], "max_scroll": geometry["max_scroll"],
                       "missing": [f for f in chat_destinations.TOP_LEVEL if not placed.get(f)], "cut": cut,
                       "ok": geometry["max_scroll"] == 0 and geometry["fits"] and popup[1] + popup[3] <= height
                       and all(placed.get(f) for f in chat_destinations.TOP_LEVEL) and not cut}
    state["laid_out"][locale] = found
    return all(entry["ok"] for entry in found.values())


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def pages(locale):
    """Every group's page at the top, middle and end of its list: no text the menu draws crosses from one band into
    another (a row over the page's header or pinned toggles, over the strip or the footer). Back to the top level."""
    from angelo_agent import chat_add_nav, chat_destinations
    popup, crossed = view()["popover"], {}
    for entry in chat_destinations.entries():
        if entry["kind"] != "group":
            continue
        chat_add_nav.open_page(popup, entry["value"])
        redraw()
        end = (popup.get("layout") or {}).get("max_scroll", 0)
        for name, part in (("top", 0.0), ("middle", 0.5), ("end", 1.0)):
            popup["scroll"] = end * part
            found = qa_plus_menu.crossings(view(), qa_plus_menu.recorded_draw(redraw))
            if found:
                crossed["%s@%s" % (entry["value"], name)] = found[:6]
            if entry["value"] == "base_mesh" and name == "middle":
                capture("plus-menu-%s-%s-base-meshes-scrolled.png" % (TAG, locale))
    chat_add_nav.back(popup)
    redraw()
    state.setdefault("pages", {})[locale] = crossed
    checks["no_row_draws_over_a_header_the_strip_or_the_footer_" + locale] = not crossed


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_PLUS_MENU_FOLD", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    prefs.view.language = 'en_US'
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def go(phase, delay=0.4):
    state["phase"] = phase
    return delay


def tick():
    from angelo_agent import director
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window()) and hit_point("attach")):
                return .3
            checks["starts_in_director_mode"] = director.is_director(window())
            if REQUESTED:   # A variant's window (--window-geometry): drawn at that size, or the run fails.
                w, pixels = window(), prefs.system.pixel_size
                state["window"] = {"requested": list(REQUESTED), "actual": [w.width, w.height], "pixel_size": pixels,
                                   "ui_scale": prefs.system.ui_scale}
                checks["the_window_is_the_requested_size"] = (
                    abs(w.width - REQUESTED[0] * pixels) <= 1 and abs(w.height - REQUESTED[1] * pixels) <= 1)
            return go("next")
        if phase == "next":   # The next drawn language, or the laid-out sweep once all are drawn.
            if not state["queue"]:
                return go("sweep", .1)
            state["locale"] = state["queue"].pop(0)
            view().pop("popover", None)
            language(state["locale"])
            return go("open", .6)
        if phase == "open":
            qa_input.press(window(), *hit_point("attach"))
            return go("opened")
        if phase == "opened":
            if not wait(phase + state["locale"], lambda: qa_plus_menu.at_top(view()) and view().get("popup_hits")):
                return .2
            _, region = chat()
            fold = qa_plus_menu.fold(view(), (region.width, region.height))
            state["drawn"][state["locale"]] = fold
            checks["drawn_every_feature_in_view_" + state["locale"]] = fold["ok"]
            capture("plus-menu-%s-%s.png" % (TAG, state["locale"]))
            pages(state["locale"])
            if state["locale"] == "en_US":   # The keyboard, once: every feature reachable, a page and back.
                state["visited"] = []
                return go("tab", .1)
            view().pop("popover", None)
            return go("next")
        if phase == "tab":   # Tab walks every stop: each top-level feature is one of them.
            popup = view().get("popover") or {}
            stops, cursor = popup.get("stops") or [], popup.get("cursor")
            if popup.get("keyboard") and cursor is not None and cursor < len(stops):
                state["visited"].append(stops[cursor][1])
            if len(state["visited"]) < len(stops) + 1 and len(state["visited"]) < 80:
                key('TAB')
                return .15
            from angelo_agent import chat_destinations
            checks["the_keyboard_reaches_every_feature"] = set(chat_destinations.TOP_LEVEL) <= set(state["visited"])
            state["missed_by_keyboard"] = sorted(set(chat_destinations.TOP_LEVEL) - set(state["visited"]))
            return go("to_flows", .1)
        if phase == "to_flows":   # On to Generation Flows, then Return opens its page.
            popup = view().get("popover") or {}
            stops, cursor = popup.get("stops") or [], popup.get("cursor")
            if cursor is not None and cursor < len(stops) and stops[cursor][1] == "flows":
                key('RET')
                return go("flows_page")
            if not wait(phase, lambda: False, limit=12):
                key('TAB')
                return .15
        if phase == "flows_page":
            if not wait(phase, lambda: qa_plus_menu.drawn_page(view()) == "flows"):
                return .2
            checks["return_opens_a_group_page"] = True
            key('ESC')
            return go("esc_back")
        if phase == "esc_back":
            if not wait(phase, lambda: qa_plus_menu.at_top(view())):
                return .2
            checks["esc_on_a_page_goes_back"] = True
            key('ESC')
            return go("esc_close")
        if phase == "esc_close":
            if not wait(phase, lambda: not view().get("popover")):
                return .2
            checks["esc_at_the_top_level_closes"] = True
            return go("next")
        if phase == "sweep":
            failed = [locale for locale in LAID_OUT if (language(locale) or True) and not laid_out(locale)]
            checks["laid_out_every_feature_in_view_at_1440x875_and_1280x775_in_21_languages"] = not failed
            state["laid_out_failed"] = failed
            checks["the_sweep_covered_21_languages"] = len(state["laid_out"]) == 21
            return go("complete", .1)
        if phase == "complete":
            finish()
            return None
        raise AssertionError("unknown phase " + phase)
    except Exception as error:  # Report and quit: a GUI suite never hangs.
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=0.5, persistent=True)
