# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Forget Agent Changes in a Folder from its two new ways in, by the person's real presses (2.1.0; the
coordinator's 2026-11-17 ruling; doc/angelo/script_confinement.rst, "What scripts wrote").

* A script's call of the operator is refused first: it is a grant (``grant_guard``), and nothing is forgotten.
* The chat bar's + menu: the person opens it, finds the row (Apps and settings), and Return on it opens the folder
  browser; Return there forgets the folder.
* The AI Providers page, Agent permissions: Browse on the row opens the folder browser; Return there forgets the
  folder.

Each step is checked by state (which operator the browser serves; the folder Angelo forgot, through the real C door)
and captured for review (``forget-*.png``). Synthetic events in Angelo's default chat startup, isolated state; no
provider, account or network. Run through ``run_native_suites.py --gui --include test_blender_forget_changes_reach``;
``forget-changes-reach.json`` has ``success`` and ``phase == "complete"``.
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

assert not bpy.app.background, "The + menu and the AI Providers page need a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402
import qa_hit  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The composer belongs to the default chat.
from angelo_agent import ui as _ui  # noqa: E402
from angelo_agent import agent_changes_forget as _forget  # noqa: E402
_ui._authentication.start = lambda *args, **kwargs: None   # No sign-in: the composer shows as connected.
_ui.connection_available = lambda: True
_ui.prepare_controller = lambda: None
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-forget-reach-qa"))
OUT.mkdir(parents=True, exist_ok=True)
FOLDERS = {name: OUT / ("reviewed-" + name) for name in ("script", "menu", "page")}
for _folder in FOLDERS.values():
    _folder.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}, "screenshots": []}
STARTED = time.monotonic()
LIMIT = 240.0 * qa_waits.timeout_scale()
SHIELD = 1.3   # Seconds after focus arrives before a key on it counts (C's SHIELD_SECONDS, with room).
ROW = ("popup_add_open", "forget_agent_changes")
OPERATOR = "ANGELO_OT_forget_agent_changes"
WAITING = {}
CALLS = []   # (folder, "ok" | "error", records forgotten or the error) for every forget the operator ran.
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False

_real_forget = _forget.forget


def _recorded(folder):
    """The operator's own call of the C door (``forget_changes``), recorded; its grant checks are unchanged."""
    try:
        count = _real_forget(folder)
    except Exception as error:   # noqa: BLE001 - recorded, then raised as the operator would see it.
        CALLS.append((folder, "error", repr(error)))
        raise
    CALLS.append((folder, "ok", count))
    return count


_forget.forget = _recorded   # The module global the operator's execute calls.


def check(name, condition, detail=None):
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]
    assert condition, name


def until(condition, label, limit=30.0):
    value = condition()
    if value:
        WAITING.pop(label, None)
        return value
    since = WAITING.setdefault(label, time.monotonic())
    assert time.monotonic() - since < limit * qa_waits.timeout_scale(), "Timed out waiting: " + label
    return None


def after(label, seconds):
    return time.monotonic() - WAITING.setdefault("after:" + label, time.monotonic()) >= seconds


def capture(name, window=None):
    window = window or bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    REPORT["screenshots"].append(name)


def key(window, kind, x, y):
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def chat_parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def chat_view():
    from angelo_agent import chat_view as canvas
    return canvas._views.get(chat_parts()[1].as_pointer(), {})


def popover():
    return chat_view().get("popover") or {}


def chat_key(kind):
    window, _area, region = chat_parts()
    key(window, kind, region.x + region.width // 2, region.y + region.height // 2)


def chat_press(action, value=None, popup=False):
    def point():
        hits = chat_view().get("popup_hits" if popup else "hits", ())
        return qa_input.chat_point(chat_parts()[2], next(r for r, a, v in hits if a == action
                                                          and (value is None or v == value)))
    qa_input.press(chat_parts()[0], point)


def step_to(row, label):
    """Down arrows until the + menu's keyboard focus is ``row``; True once it is. Fails after sixteen rows."""
    from angelo_agent import chat_popovers
    if tuple(chat_popovers.key_focus(chat_view())) == row:
        return True
    steps = REPORT.setdefault("steps", {})
    if time.monotonic() - steps.get(label, (0, 0.0))[1] >= 0.3:
        count = steps.get(label, (0, 0.0))[0] + 1
        assert count <= 16, "the + menu never reached %r: %r" % (row, chat_popovers.key_focus(chat_view()))
        steps[label] = (count, time.monotonic())
        chat_key('DOWN_ARROW')
    return False


def prefs_parts():
    for window in bpy.context.window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == 'PREFERENCES'), None)
        if area is not None:
            return window, area, next(r for r in area.regions if r.type == 'WINDOW')
    return None, None, None


def page_row(action, value, label):
    """The AI Providers page's hits once control ``(action, value)`` is wholly in view (scrolling toward it)."""
    from angelo_agent import providers_page_host as page
    window, area, region = prefs_parts()
    plan = page.plan_for(region) if region else None
    rect = next((r for r, a, v in (plan.hits if plan else ()) if a == action and v == value), None)
    if rect is not None and 24 <= rect[1] and rect[1] + rect[3] <= region.height - 24:
        return plan.hits
    if until(lambda: False, label, 40) is None and rect is not None:
        scroll = bpy.ops.view2d.scroll_down if rect[1] < 24 else bpy.ops.view2d.scroll_up
        with bpy.context.temp_override(window=window, area=area, region=region):
            scroll()
    return None


def page_press(hits, action, value=None):
    window, _area, region = prefs_parts()
    rect = next(r for r, a, v in hits if a == action and (value is None or v == value))
    qa_input.press(window, *qa_hit.point(qa_hit.PIXELS, region, rect))   # The page lays out in region pixels.


def browser():
    """``(window, area)`` of an open file browser serving the Forget operator, else None."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            operator = getattr(area.spaces.active, "active_operator", None) if area.type == 'FILE_BROWSER' else None
            if operator is not None and operator.bl_idname == OPERATOR:
                return window, area
    return None


def any_browser():
    return any(a.type == 'FILE_BROWSER' for w in bpy.context.window_manager.windows for a in w.screen.areas)


def confirm(found, folder):
    """The browser at ``folder`` (where the person navigated), then the person's Return over its file list."""
    window, area = found
    area.spaces.active.params.directory = str(folder).encode()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    key(window, 'RET', region.x + region.width // 2, region.y + region.height // 2)


def forgot(folder):
    return next((call for call in CALLS if call[0].rstrip("/\\") == str(folder)), None)


def tick():
    from angelo_agent import chat_add_nav, console, ui
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        if phase == "boot" and not (ui.main_window() and qa_boot.started() and any(
                console.is_chat(a) for a in ui.main_window().screen.areas) and chat_view().get("hits")):
            return 0.2
        if phase == "boot":
            window, area, region = chat_parts()
            from angelo_agent import grant_guard
            with bpy.context.temp_override(window=window, area=area, region=region):
                try:
                    result = str(bpy.ops.angelo.forget_agent_changes('EXEC_DEFAULT', directory=str(FOLDERS["script"])))
                except (RuntimeError, PermissionError) as error:
                    result = str(error)
            check("a_scripted_forget_is_refused", (grant_guard.NOT_YOURS in result or grant_guard.SCRIPT_RUNS in result
                                                   or "CANCELLED" in result) and not forgot(FOLDERS["script"])
                  and not any_browser(), (result, CALLS))
            chat_press("attach")   # The person opens the + menu.
            REPORT["phase"] = "plus_query"
        elif phase == "plus_query":
            if not until(lambda: popover().get("kind") == "add", "the + menu"):
                return 0.1
            chat_add_nav.set_query(popover(), "Forget Agent")
            REPORT["phase"] = "plus_step"
        elif phase == "plus_step":
            if not (after("plus_step", 0.3) and step_to(ROW, "plus_step")):
                return 0.1
            REPORT["phase"] = "plus_ret"
        elif phase == "plus_ret":
            if not after("plus_ret", SHIELD):
                return 0.1
            rows = [row for row in popover().get("rows", ()) if row.get("value") == ROW[1]]
            check("the_plus_menu_finds_the_row", bool(rows), popover().get("rows"))
            REPORT["details"]["plus_row"] = {key: str(rows[0].get(key)) for key in ("label", "subtitle", "operator")}
            capture("forget-plus-menu-row.png")
            chat_key('RET')
            REPORT["phase"] = "plus_picker"
        elif phase == "plus_picker":
            found = until(browser, "the folder browser from the + menu")
            if not found:
                return 0.2
            check("the_plus_menu_row_opens_the_forget_folder_browser", True)
            capture("forget-plus-menu-picker.png", found[0])
            REPORT["phase"] = "plus_confirm"
        elif phase == "plus_confirm":
            found = browser()
            if not (found and after("plus_confirm", SHIELD)):
                return 0.1
            confirm(found, FOLDERS["menu"])
            REPORT["phase"] = "plus_forgot"
        elif phase == "plus_forgot":
            if not until(lambda: forgot(FOLDERS["menu"]), "the folder forgotten from the + menu"):
                return 0.2
            call = forgot(FOLDERS["menu"])
            check("the_persons_return_forgets_the_folder_from_the_plus_menu", call[1] == "ok", call)
            check("the_plus_menus_browser_closes", not any_browser())
            with bpy.context.temp_override(window=chat_parts()[0]):
                bpy.ops.angelo.settings()   # The AI Providers page.
            REPORT["phase"] = "page"
        elif phase == "page":
            hits = page_row("forget_agent_changes", "", "the Forget row's Browse in view")
            if hits is None:
                return 0.2
            capture("forget-providers-page-row.png", prefs_parts()[0])
            page_press(hits, "forget_agent_changes", "")
            REPORT["phase"] = "page_picker"
        elif phase == "page_picker":
            found = until(browser, "the folder browser from the AI Providers page")
            if not found:
                return 0.2
            check("browse_on_the_page_row_opens_the_forget_folder_browser", True)
            capture("forget-providers-page-picker.png", found[0])
            REPORT["phase"] = "page_confirm"
        elif phase == "page_confirm":
            found = browser()
            if not (found and after("page_confirm", SHIELD)):
                return 0.1
            confirm(found, FOLDERS["page"])
            REPORT["phase"] = "page_forgot"
        elif phase == "page_forgot":
            if not until(lambda: forgot(FOLDERS["page"]), "the folder forgotten from the AI Providers page"):
                return 0.2
            call = forgot(FOLDERS["page"])
            check("the_persons_return_forgets_the_folder_from_the_page", call[1] == "ok", call)
            check("nothing_else_was_forgotten", [c[0].rstrip("/\\") for c in CALLS]
                  == [str(FOLDERS["menu"]), str(FOLDERS["page"])], CALLS)
            return finish()
        return 0.3
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2),
                  calls=[list(map(str, call)) for call in CALLS])
    if error:
        REPORT["error"] = error
    (OUT / "forget-changes-reach.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_FORGET_CHANGES_REACH", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=1.0)   # Never ahead of a press.
