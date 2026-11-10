# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feedback one click away, and no donation link, in a windowed Angelo in Director Mode (doc/angelo/app_identity.rst,
"Links"; doc/angelo/features.rst, "Send feedback").

Real default startup and real input (``Window.event_simulate``); no account, network or browser: the link a click
would open is recorded (``update_card.open_link``, the one place notices and feedback leave the app). Steps, each
captured: the chat bar's + opens Angelo's menu, whose footer offers Report a bug… and Request a feature… in view at
once, with nothing scrolled (1.0.8); a real click on Report a bug… opens the feedback form with kind, version, build, system and graphics card
in the link's fragment and nothing personal; Request a feature… the same without the graphics card; the Help menu
(opened as a person opens it) offers Report a Bug and Request a Feature, and its presets open the same form; About
offers Credits, License and blender.org and no Donate or Support Blender; in Japanese the + menu's Feedback rows and
the Help entry read in Japanese.

Run through ``run_native_suites.py --gui --include test_blender_feedback_gui``; check ``feedback-gui.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
import urllib.parse
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (synthetic clicks)
import qa_plus_menu  # noqa: E402  (the + menu's fold, read from the chat's canvas state)

product_env.clear("DISABLE_DEFAULT_CHAT")
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-feedback-gui"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "feedback-gui.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "opened": []}
checks = state["checks"]
started = time.monotonic()
HOME = os.path.expanduser("~")


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def record_links():
    from angelo_agent import update_card
    update_card.open_link = lambda url: state["opened"].append(url)


def chat_view_state():
    from angelo_agent import chat_view, console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return chat_view._views.get(area.as_pointer(), {}) if area else {}


def hit_point(action, value=None):
    """Window coordinates of a chat control's centre (its hit rectangle, region pixels), or None while not drawn. The
    + menu's rows are in ``popup_hits`` (chat_add_menu), the chat's own controls in ``hits``."""
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    region = next(r for r in area.regions if r.type == 'WINDOW')
    view = chat_view_state()
    rect = next((r for r, a, v in view.get("popup_hits", []) + view.get("hits", [])
                 if a == action and (value is None or v == value)), None)
    return None if rect is None else (region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2)


def key(kind):
    w = window()
    x, y = (int(v) for v in hit_point("attach"))
    w.event_simulate(type=kind, value='PRESS', x=x, y=y + 80)
    w.event_simulate(type=kind, value='RELEASE', x=x, y=y + 80)


def menu_open():
    return (chat_view_state().get("popover") or {}).get("kind") == "add"


def fragment(url):
    base, _, frag = url.partition("#")
    return base, dict(urllib.parse.parse_qsl(frag))


def check_link(prefix, url, kind, with_gpu):
    import angelo_identity
    base, values = fragment(url)
    checks[prefix + "_opens_the_feedback_form"] = base == angelo_identity.url("feedback") and "?" not in url
    checks[prefix + "_kind"] = values.get("kind") == kind
    checks[prefix + "_version_build_and_system_filled_in"] = (
        angelo_identity.APP_VERSION in values.get("version", "") and bool(values.get("build"))
        and bool(values.get("os")))
    checks[prefix + "_graphics_card_only_for_a_bug"] = bool(values.get("gpu")) == with_gpu
    checks[prefix + "_only_allowed_details"] = set(values) <= {"kind", *angelo_identity.FEEDBACK_FIELDS}
    checks[prefix + "_nothing_personal"] = HOME not in urllib.parse.unquote(url) and "branch" not in url
    state[prefix + "_details"] = values


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FEEDBACK_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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


def open_menu(next_phase):
    """Click +: the menu opens at its top level, the footer's feedback buttons in view (1.0.8; nothing to scroll)."""
    qa_input.press(window(), *hit_point("attach"))
    state["after_open"] = next_phase
    return go("opening")


def tick():
    from angelo_agent import about_card, card_host, director, ui_text
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window()) and hit_point("attach")):
                return .3
            checks["starts_in_director_mode"] = director.is_director(window())
            record_links()
            return open_menu("bug_row")
        if phase == "opening":
            if not wait(phase + state["after_open"], lambda: qa_plus_menu.at_top(chat_view_state())):
                return .2
            return go(state["after_open"] + "_shown", .1)
        if phase in {"bug_row_shown", "feature_row_shown"}:
            row = "report_bug" if phase == "bug_row_shown" else "request_feature"
            if not wait(phase, lambda: hit_point("popup_add_open", row) is not None):
                return .2
            view = chat_view_state()
            rows = view["popover"]["rows"]
            checks["menu_feedback_entries_are_its_footer_buttons"] = [
                r.get("value") for r in rows if r.get("placement") == "footer"] == ["report_bug", "request_feature"]
            box = view.get("popup_rect")
            shown = [qa_plus_menu.hit(view, "popup_add_open", value) for value in ("report_bug", "request_feature")]
            checks["menu_feedback_buttons_in_view_without_scrolling"] = (
                (view["popover"].get("layout") or {}).get("max_scroll") == 0 and bool(box) and all(
                    rect and qa_plus_menu.inside(rect, box) for rect in shown))
            if phase == "bug_row_shown":
                capture("feedback-1-plus-menu-director.png")
            state["clicks_before"] = len(state["opened"])
            qa_input.press(window(), *hit_point("popup_add_open", row))
            return go(row + "_clicked")
        if phase == "report_bug_clicked":
            if not wait(phase, lambda: len(state["opened"]) > state["clicks_before"]):
                return .2
            check_link("report_bug_row", state["opened"][-1], "bug", True)
            checks["menu_closes_after_the_click"] = not menu_open()
            return open_menu("feature_row")
        if phase == "request_feature_clicked":
            if not wait(phase, lambda: len(state["opened"]) > state["clicks_before"]):
                return .2
            check_link("request_feature_row", state["opened"][-1], "feature", False)
            # Help: the presets the Help menu's entries run, and the menu itself as a person opens it.
            preset = bpy.types.WM_OT_url_open_preset.lookup_url_from_type
            check_link("help_bug", preset("BUG"), "bug", True)
            check_link("help_feature", preset("FEATURE"), "feature", False)
            checks["help_has_no_fund_preset"] = preset("FUND") is None
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.call_menu(name="TOPBAR_MT_help")
            return go("help_shown", .6)
        if phase == "help_shown":
            capture("feedback-2-help-menu.png")
            key('ESC')
            bpy.ops.angelo.show_about()
            return go("about", .8)
        if phase == "about":
            if not wait(phase, lambda: card_host.is_active("about")):
                return .2
            plan = card_host._active.plan
            actions = {action for _, action, _ in plan.hits}
            texts = [op[1] for op in plan.ops if op[0] in {"text", "mono"}]
            state["about_actions"], state["about_texts"] = sorted(actions), texts
            checks["about_keeps_credits_license_and_blender_org"] = {"credits", "license", "blender"} <= actions
            checks["about_offers_no_donation"] = not ({"donate", "fund"} & actions) and not any(
                "Donate" in t or "Support Blender" in t for t in texts)
            capture("feedback-3-about.png")
            card_host._active.card.act("close", None, card_host.Host(card_host._active))
            prefs.view.language = 'ja_JP'
            prefs.view.use_translate_interface = True
            return go("japanese", .8)
        if phase == "japanese":
            checks["japanese_row_reads_in_japanese"] = ui_text.iface_("Report a bug…") == "バグを報告…"
            checks["japanese_section_reads_in_japanese"] = ui_text.iface_("Feedback") == "フィードバック"
            checks["japanese_help_entry_reads_in_japanese"] = (
                bpy.app.translations.pgettext_iface("Request a Feature", "Operator") == "機能をリクエスト")
            return open_menu("japanese_row")
        if phase == "japanese_row_shown":
            if not wait(phase, lambda: hit_point("popup_add_open", "report_bug") is not None):
                return .2
            capture("feedback-4-plus-menu-japanese.png")
            key('ESC')
            return go("complete", .3)
        if phase == "complete":
            finish()
            return None
        raise AssertionError("unknown phase " + phase)
    except Exception as error:  # Report and quit: a GUI suite never hangs.
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=0.5, persistent=True)
