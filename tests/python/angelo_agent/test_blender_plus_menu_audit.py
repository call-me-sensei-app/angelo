# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every state of the chat bar's + menu, captured and checked, in a windowed Angelo in Director Mode (1.0.8 audit).

Two menu defects reached 1.0.8 cuts (a fit toggle scrolled out of view; a scrolled page's rows drawn over its header
and the hint), so this suite captures the chat area in every state a person meets, for people to look at, and checks
each the same way: no text the menu draws crosses from one band into another (``qa_plus_menu.crossings``: bar, a
page's header and pinned toggles, list, strip, footer), the popup inside the chat, and the top level scrolls only in a
one-column chat. States, in English, German, Japanese, Swedish and Arabic (right to left), at the window each variant
starts (``PLUS_MENU_WINDOWS``): the top level; a tile with the keyboard's focus (its description in the strip); every
group's page at the top, middle and end of its list; the base meshes' page with a mesh chosen and its fit toggle; a
search with results and one with none; and the top level in a one-column chat (a larger interface scale).

Three more checks, each for a class of defect people found in its captures that the checks above missed (1.0.8):
a drawn text cut with an ellipsis its text does not have, or running past the popup's sides (``cut_texts``,
``qa_plus_menu.overflowing``); a shape or stroke that cannot be seen against what lies under it, as a translucent
cut-out over a teal rim (``qa_plus_menu.invisible``); and, in each language, every feature and every row inside a group
found by searching its name as shown and its English name (``search_misses``).

Run through ``run_native_suites.py --gui --include test_blender_plus_menu_audit``; each run's ``plus-menu-audit.json``
lists every state's capture and findings, and ``success`` needs none.
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
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_plus_menu  # noqa: E402  (the + menu's bands and fold, read from the chat's canvas state)

product_env.clear("DISABLE_DEFAULT_CHAT")
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-plus-menu-audit"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "plus-menu-audit.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
LANGUAGES = ("en_US", "de_DE", "ja_JP", "sv_SE", "ar_EG")
REQUESTED = tuple(int(v) for v in os.environ["ANGELO_PLUS_MENU_WINDOW"].split("x")) \
    if os.environ.get("ANGELO_PLUS_MENU_WINDOW") else None
TAG = "%dx%d" % REQUESTED if REQUESTED else "default"
state = {"phase": "boot", "success": False, "checks": {}, "states": [], "queue": list(LANGUAGES)}
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


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def capture(name):
    """The window as drawn (as every GUI suite captures it); the chat's rect is recorded beside it."""
    path = OUT / ("%s-%s.png" % (TAG, name))
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    return str(path)


def audit(name, top_level=False):
    """Check the menu as it is now drawn, capture it and record the state."""
    _, region = chat()
    records = qa_plus_menu.recorded_draw(redraw)
    popup = view().get("popover") or {}
    geometry = popup.get("layout") or {}
    box = view().get("popup_rect")
    from angelo_agent import chat_view
    findings = {"crossings": qa_plus_menu.crossings(view(), records), "cut": cut_texts(records, popup),
                "overflow": qa_plus_menu.overflowing(view(), records),
                "invisible": qa_plus_menu.invisible(qa_plus_menu.recorded_shapes(redraw), chat_view.POPOVER)}
    if box and not qa_plus_menu.inside(box, (0, 0, region.width, region.height)):
        findings["outside_the_chat"] = [round(v, 1) for v in box]
    if top_level and geometry.get("columns") == 2 and geometry.get("max_scroll"):
        findings["top_level_scrolls"] = geometry["max_scroll"]
    findings = {key: value for key, value in findings.items() if value}
    area, _ = chat()
    state["states"].append({"language": state["locale"], "state": name, "capture": capture(state["locale"] + "-" + name),
                            "chat": [region.width, region.height], "columns": geometry.get("columns"),
                            "chat_in_window": [area.x, area.y, area.width, area.height],
                            "window": [window().width, window().height], "findings": findings})
    return not findings


def cut_texts(records, popup):
    """Drawn texts that end in "…" where the text they show does not (a title or description cut, never allowed): a
    label's own "…" ("Image or file…") is the end of a full text the menu shows."""
    from angelo_agent import chat_add_menu as menu, chat_add_nav as nav
    from angelo_agent.ui_text import iface_
    shown = {iface_(nav.HINT)}
    for row in list(popup.get("rows") or ()) + list(popup.get("entries") or ()):
        shown.update(text for text in (menu._shown(row, "label"), menu._shown(row, "subtitle")) if text)
    return sorted({value for value, _, _ in records
                   if value.rstrip().endswith("…") and not any(full.rstrip().endswith(value.rstrip()) for full in shown)})


def search_misses():
    """Every feature and every row inside a group, searched by its name as shown (the person's language) and as
    written (its English name; a product name such as EEVEE is both): each search must find that row."""
    from angelo_agent import chat_add_menu as menu, chat_add_nav as nav, chat_destinations
    rows = list(chat_destinations.entries())
    for entry in chat_destinations.entries():
        if entry["kind"] == "group":
            rows += chat_destinations.group_rows(entry["value"], (), "", False)
    misses = []
    for row in rows:
        if row.get("kind") not in ("row", "group"):
            continue
        written = row.get("label") if isinstance(row.get("label"), str) else ""
        for kind, query in (("shown", menu._shown(row, "label")), ("written", written)):
            if not query:
                continue
            found = nav.search(query, (), "", False, menu._shown)
            if not any((hit.get("action"), hit.get("value")) == (row["action"], row["value"]) for hit in found):
                misses.append([kind, query, repr(row.get("value"))])
    return misses


def language(locale):
    available = {item.identifier for item in prefs.view.bl_rna.properties["language"].enum_items}
    if locale not in available:
        raise AssertionError("this build offers no %s interface" % locale)
    prefs.view.language, prefs.view.use_translate_interface = locale, True


def open_menu():
    from angelo_agent import chat_view
    area, _ = chat()
    view().pop("popover", None)
    with bpy.context.temp_override(window=window(), area=area):
        chat_view.show_popover(bpy.context, "add")
    redraw()


def every_state(locale):
    """Every state in this language; True when none has a finding."""
    from angelo_agent import chat_add_nav, chat_destinations, chosen_tools, ui
    ok = True
    open_menu()
    popup = view()["popover"]
    ok &= audit("top", top_level=True)
    tile = next((stop for stop in popup.get("stops") or () if stop[0] == "popup_add_group"), None)
    if tile is not None:   # The keyboard's focus on a tile: its ring and its description in the strip.
        popup.update(cursor=popup["stops"].index(tile), keyboard=True)
        ok &= audit("keyboard-focus", top_level=True)
        popup.update(cursor=None, keyboard=False)
    for entry in chat_destinations.entries():
        if entry["kind"] != "group":
            continue
        chat_add_nav.open_page(popup, entry["value"])
        redraw()
        end = (popup.get("layout") or {}).get("max_scroll", 0)
        for name, part in (("top", 0.0), ("middle", 0.5), ("end", 1.0)):
            popup["scroll"] = end * part
            ok &= audit("page-%s-%s" % (entry["value"], name))
        chat_add_nav.back(popup)
    controller = ui.get_controller()   # A chosen mesh: its page opens on it, the fit toggle pinned above.
    saved = list(getattr(controller, "chosen_tools", []) or [])
    option = chosen_tools.tool("base_mesh").options[len(chosen_tools.tool("base_mesh").options) // 2][0]
    controller.chosen_tools = chosen_tools.choose([], "base_mesh", option)
    chat_add_nav.open_page(popup, "base_mesh")
    ok &= audit("page-base_mesh-chosen")
    controller.chosen_tools = saved
    chat_add_nav.back(popup)
    for name, query in (("search-results", "render"), ("search-none", "zzqx")):
        chat_add_nav.set_query(popup, query)
        ok &= audit(name)
        found = [row for row in popup.get("rows") or () if row.get("kind") in ("row", "group")]
        if (name == "search-results") != bool(found):   # "render" finds the render tools in every language.
            state["states"][-1]["findings"]["results"] = len(found)
            ok = False
    chat_add_nav.set_query(popup, "")
    view().pop("popover", None)
    redraw()
    return ok


def narrow():
    """A one-column chat as people get one: a larger interface scale (Preferences > Interface) narrows the chat in
    the canvas's units below what two columns need. Audit the top level and its end, then put the scale back."""
    from angelo_agent import chat_add_layout
    _, region = chat()
    scale = round(region.width / (chat_add_layout.TWO_COLUMNS + 2 * chat_add_layout.MARGIN - 20), 2)
    prefs.view.ui_scale = scale
    try:
        redraw()
        open_menu()
        popup = view()["popover"]
        state["narrow"] = {"ui_scale": scale, "chat": [region.width, region.height],
                           "columns": (popup.get("layout") or {}).get("columns")}
        ok = state["narrow"]["columns"] == 1 and audit("narrow-top", top_level=True)
        popup["scroll"] = (popup.get("layout") or {}).get("max_scroll", 0)
        ok &= audit("narrow-top-end", top_level=True)
        view().pop("popover", None)
    finally:
        prefs.view.ui_scale = 1.0
        redraw()
    return ok


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["findings"] = [entry for entry in state["states"] if entry["findings"]]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_PLUS_MENU_AUDIT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    prefs.view.language = 'en_US'
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import director
    try:
        assert time.monotonic() - started < 280, "Suite timed out"
        phase = state["phase"]
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window()) and chat()[0] is not None
                    and view().get("hits")):
                return .3
            checks["starts_in_director_mode"] = director.is_director(window())
            if REQUESTED:
                w, pixels = window(), prefs.system.pixel_size
                state["window"] = {"requested": list(REQUESTED), "actual": [w.width, w.height], "pixel_size": pixels}
                checks["the_window_is_the_requested_size"] = (
                    abs(w.width - REQUESTED[0] * pixels) <= 1 and abs(w.height - REQUESTED[1] * pixels) <= 1)
            state["phase"] = "languages"
            return .2
        if phase == "languages":
            if not state["queue"]:
                state["phase"] = "narrow"
                return .2
            state["locale"] = state["queue"].pop(0)
            language(state["locale"])
            for area in window().screen.areas:   # Every editor redraws in the new language (the top bar too)
                area.tag_redraw()                # once the event loop has run: the states come on the next tick.
            state["phase"] = "states"
            return .6
        if phase == "states":
            checks["every_state_is_clean_" + state["locale"]] = every_state(state["locale"])
            misses = search_misses()
            state.setdefault("search_misses", {})[state["locale"]] = misses[:20]
            checks["search_finds_every_feature_by_its_shown_and_english_name_" + state["locale"]] = not misses
            state["phase"] = "languages"
            return .2
        if phase == "narrow":
            if state.get("locale") != "en_US":
                state["locale"] = "en_US"
                language("en_US")
                for area in window().screen.areas:
                    area.tag_redraw()
                return .6
            checks["a_one_column_chat_is_clean"] = narrow()
            state["phase"] = "complete"
            return .1
        if phase == "complete":
            finish()
            return None
        raise AssertionError("unknown phase " + phase)
    except Exception as error:  # Report and quit: a GUI suite never hangs.
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
