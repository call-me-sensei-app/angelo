# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A person's press that comes before a grant control armed does nothing, quietly, and the control says what works
(doc/angelo/grant_operators.rst, "Authority in C"; the coordinator's ruling on the 0.3 s arm, 2026-11-16).

A control that reaches a grant takes a press only after the person's real move onto it once it has been on screen
``ARM_SECONDS``. A faster press, on the chat canvas or on Blender's own button, shows no error report, toast or "Only
your own click…": the control shows a hint (``grant_hints``) and grants nothing. A small move inside it afterwards (a
hand's jitter, no leaving the control) arms it, and the next press grants:

* the composer's approval popover's "Approve for me" row, pressed in the pass the popover first shows: nothing
  changes, the hint shows, no report; one pixel's move inside the row and a press there then switch the chat to Auto;
* the Approval Policy menu's "Trust scripts for this chat…" item (Blender's own button, a grant operator), pressed in
  the pass the menu first shows: Blender's menu takes no click then (the item stays, nothing runs), so no dialog, no
  grant and no report;
* the same item pressed a pass or two after it shows, still inside the arm (the review of 69d589a, SHOULD 4): the
  press reaches the operator unarmed, so the item hints and nothing opens, grants or reports; pressed as a person
  does, it opens the dialog;
* a guarded preference's own checkbox (``allow_local_cli``, a ``prop.*`` grant) pressed the same way: it stays off
  and hints; pressed as a person does, it turns on.
A case whose press lands ``ARM`` or later after the control first showed (a stalled machine) proves nothing about a
fast press and runs again, up to ``TRIES`` times, each counted in ``details``.

Synthetic events in Angelo's default chat startup, isolated state; no provider, account, network or paid call. Run
through ``run_native_suites.py --gui --include test_blender_fast_press``; ``fast-press.json`` has ``success`` and
``phase == "complete"``.
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

assert not bpy.app.background, "The fast press suite needs a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The composer belongs to the default chat.
from angelo_agent import ui as _ui  # noqa: E402
_ui._authentication.start = lambda *args, **kwargs: None   # No sign-in: the composer shows as connected.
_ui.connection_available = lambda: True
_ui.prepare_controller = lambda: None
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-fast-press-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}}
STARTED = time.monotonic()
LIMIT = 150.0 * qa_waits.timeout_scale()
ARM = 0.6   # Past C's ARM_SECONDS (0.3) with room.
SOON = 0.06   # A pass or two after a control first shows: well inside C's ARM_SECONDS.
INSIDE = 0.25   # A "soon" press that landed later than this after the control showed is retried (a stall).
TRIES = 4
TOGGLE = "Allow apps on this computer to reach Angelo"   # allow_local_cli's checkbox, a prop.* grant.
WAITING = {}
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def check(name, condition, detail=None):
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]


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


def chat_parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def chat_view():
    from angelo_agent import chat_view as canvas
    return canvas._views.get(chat_parts()[1].as_pointer(), {})


def point(action, value=None, popup=False):
    rect = next(r for r, a, v in chat_view().get("popup_hits" if popup else "hits", ())
                if a == action and (value is None or v == value))
    return qa_input.chat_point(chat_parts()[2], rect)


def auto_row():
    return point("popup_approval", "AUTO_BOUNDED", popup=True)


def approval_popover():
    return (chat_view().get("popover") or {}).get("kind") == "approval" and any(
        a == "popup_approval" and v == "AUTO_BOUNDED" for _r, a, v in chat_view().get("popup_hits", ()))


def trust_item(window):
    from _bpy import _angelo_jobs
    found = next(((x, y, w, h) for pointer, _block, _label, idname, x, y, w, h in _angelo_jobs.popup_buttons()
                  if pointer == window.as_pointer() and w > 0
                  and idname in ("angelo.trusted_scripts", qa_input.c_idname("angelo.trusted_scripts"))), None)
    return (int(found[0] + found[2] / 2), int(found[1] + found[3] / 2)) if found else None


def toggle_box(window):
    from _bpy import _angelo_jobs
    found = next(((x, y, w, h) for pointer, _block, label, _idname, x, y, w, h in _angelo_jobs.popup_buttons()
                  if pointer == window.as_pointer() and w > 0 and TOGGLE in label), None)
    return (int(found[0] + min(found[2], found[3]) / 2), int(found[1] + found[3] / 2)) if found else None


def open_toggle(window, area, region):
    """Angelo's own code opens a popup with the guarded checkbox (as the trust menu above is opened)."""
    prefs = __import__("angelo_agent.ui", fromlist=["preferences"]).preferences()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.context.window_manager.popup_menu(lambda menu, _c: menu.layout.prop(prefs, "allow_local_cli"),
                                              title="Guard")


def soon_case(name, window, where, opener):
    """One "soon" press: wait for the control, then a quick move onto it and a press SOON later. True once pressed
    inside the arm; a press that landed later (a stall) closes the popup and opens it again (``opener``)."""
    seen = REPORT.setdefault("soon", {}).setdefault(name, {"tries": 0})
    if "shown" not in seen:
        if not until(lambda: where(window), "the control for " + name):
            return None
        seen["shown"] = time.monotonic()
        return None
    if time.monotonic() - seen["shown"] < SOON:
        return None
    seen["pressed_at"] = time.monotonic()
    fast_click(window, *where(window))
    late = time.monotonic() - seen.pop("shown")
    seen["tries"] += 1
    seen.setdefault("pressed_after", []).append(round(late, 3))
    if late <= INSIDE:
        return True
    assert seen["tries"] < TRIES, "every soon press of %s landed after the arm: %s" % (name, seen["pressed_after"])
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type='ESC', value=value)
    opener()
    return None


def move(window, x, y):
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def fast_click(window, x, y):
    """The person's quick hand: onto the control and pressed in the same pass, as it first shows."""
    move(window, x, y)
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type='LEFTMOUSE', value=value, x=int(x), y=int(y))


def refusal_reports():
    """Reports the window manager holds that say a click was not the person's (none may show for a fast press)."""
    from _bpy import _angelo_jobs
    from angelo_agent import grant_guard
    from angelo_agent.ui_text import rpt_
    said = (grant_guard.NOT_YOURS, rpt_(grant_guard.NOT_YOURS), "Only your own click")
    return [message for _kind, message in _angelo_jobs.window_reports() if any(s in message for s in said)]


def hinted(since=None):
    """A hint on screen; with ``since``, one shown at or after that moment (``HINT_SECONDS`` keeps an earlier one)."""
    from angelo_agent import grant_hints
    if since is None:
        return bool(grant_hints.showing())
    return any(until - grant_hints.HINT_SECONDS >= since for _rect, until in grant_hints._shown.values())


def tick():
    from angelo_agent import console, grant_authority, ui
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        controller = ui.get_controller()
        if phase == "boot" and not (ui.main_window() and qa_boot.started() and any(
                console.is_chat(a) for a in ui.main_window().screen.areas) and chat_view().get("hits")):
            return 0.2
        window, area, region = chat_parts()
        in_auto = lambda: grant_authority.chat_in_auto(controller.state["id"])   # noqa: E731
        if phase == "boot":
            controller.set_document(ui.document_key())
            controller.set_options(approval="ASK")
            REPORT["reports_before"] = len(refusal_reports())
            qa_input.press(window, lambda: point("approval"))   # The person opens the approval popover.
            REPORT["phase"] = "popover"
        elif phase == "popover":
            if not until(approval_popover, "the approval popover"):
                return 0.02
            x, y = auto_row()   # The pass it first shows: a quick hand onto "Approve for me" and a press.
            move(window, x, y - 120)
            fast_click(window, x, y)
            REPORT["at"] = (x, y)
            REPORT["phase"] = "popover_check"
        elif phase == "popover_check":
            if not after(phase, 1.0):
                return 0.1
            check("a_fast_press_grants_nothing", not in_auto()
                  and controller.state["options"].get("approval") != "AUTO_BOUNDED")
            check("a_fast_press_shows_the_hint_on_the_control", hinted())
            check("a_fast_press_shows_no_error", len(refusal_reports()) == REPORT["reports_before"],
                  refusal_reports())
            if not approval_popover():
                qa_input.press(window, lambda: point("approval"))
            REPORT["phase"] = "jitter"
        elif phase == "jitter":
            if not until(lambda: approval_popover() and after(phase, ARM), "the popover for the jitter"):
                return 0.1
            x, y = auto_row()
            move(window, x, y)          # On the row,
            move(window, x + 1, y)      # a hand's jitter inside it, never leaving,
            for value in ('PRESS', 'RELEASE'):   # and a press where it rests.
                window.event_simulate(type='LEFTMOUSE', value=value, x=x + 1, y=y)
            REPORT["phase"] = "jitter_check"
        elif phase == "jitter_check":
            if not until(in_auto, "Auto after a move inside the row and a press", 8):
                return 0.1
            check("a_move_inside_then_a_press_grants", True)
            controller.set_options(approval="ASK")
            move(window, window.width // 2, window.height // 2)
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_menu(name="ANGELO_MT_approval")   # Angelo's own code opens it under the pointer.
            REPORT["reports_before"] = len(refusal_reports())
            REPORT["phase"] = "menu"
        elif phase == "menu":
            if not until(lambda: trust_item(window), "the Trust scripts item"):
                return 0.02
            fast_click(window, *trust_item(window))   # As the menu first shows.
            REPORT["phase"] = "menu_check"
        elif phase == "menu_check":
            if not after(phase, 1.0):
                return 0.1
            from _bpy import _angelo_jobs
            REPORT["details"]["menu_dialog"] = list(_angelo_jobs.close_dialog_texts())[:3]
            REPORT["details"]["menu_after"] = {"reports": _angelo_jobs.window_reports()[-3:],
                                               "item_still_drawn": trust_item(window), "sent": qa_input.sent[-2:]}
            check("a_fast_press_on_blenders_button_opens_nothing", not _angelo_jobs.close_dialog_texts()
                  and not controller.trusted_scripts, REPORT["details"]["menu_dialog"])
            check("a_fast_press_on_blenders_button_shows_no_error",
                  len(refusal_reports()) == REPORT["reports_before"], refusal_reports())
            for value in ('PRESS', 'RELEASE'):   # Whatever is left of the menu closes.
                window.event_simulate(type='ESC', value=value)
            move(window, window.width // 2, window.height // 2)
            REPORT["phase"] = "menu_again"
        elif phase == "menu_again":
            if not after(phase, 0.5):
                return 0.1
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_menu(name="ANGELO_MT_approval")
            REPORT["phase"] = "menu_person"
        elif phase == "menu_person":
            if not until(lambda: trust_item(window) and after(phase, 0.5), "the Trust scripts item again"):
                return 0.1
            qa_input.press(window, lambda: trust_item(window))   # A person's move onto it, then the press.
            REPORT["phase"] = "menu_person_check"
        elif phase == "menu_person_check":
            from _bpy import _angelo_jobs
            if not until(lambda: _angelo_jobs.close_dialog_texts(), "the trust dialog from the person's press", 8):
                return 0.1
            check("the_persons_press_on_blenders_button_opens_its_dialog", True)
            for value in ('PRESS', 'RELEASE'):
                window.event_simulate(type='ESC', value=value)
            move(window, window.width // 2, window.height // 2)
            REPORT["phase"] = "menu_soon_open"
        elif phase == "menu_soon_open":
            if not after(phase, 0.5):
                return 0.1
            from _bpy import _angelo_jobs
            if _angelo_jobs.close_dialog_texts():   # The person's dialog is still closing.
                return 0.1
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_menu(name="ANGELO_MT_approval")
            REPORT["reports_before"] = len(refusal_reports())
            REPORT["phase"] = "menu_soon"
        elif phase == "menu_soon":
            def reopen():
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.wm.call_menu(name="ANGELO_MT_approval")
            if not soon_case("menu", window, trust_item, reopen):
                return 0.01
            REPORT["phase"] = "menu_soon_check"
        elif phase == "menu_soon_check":
            if not after(phase, 1.0):
                return 0.1
            from _bpy import _angelo_jobs
            REPORT["details"]["menu_soon"] = {"dialog": list(_angelo_jobs.close_dialog_texts())[:3],
                                              "reports": _angelo_jobs.window_reports()[-3:]}
            check("a_soon_press_on_blenders_button_opens_nothing", not _angelo_jobs.close_dialog_texts()
                  and not controller.trusted_scripts, REPORT["details"]["menu_soon"])
            check("a_soon_press_on_blenders_button_shows_the_hint", hinted(REPORT["soon"]["menu"]["pressed_at"]))
            check("a_soon_press_on_blenders_button_shows_no_error",
                  len(refusal_reports()) == REPORT["reports_before"], refusal_reports())
            for value in ('PRESS', 'RELEASE'):
                window.event_simulate(type='ESC', value=value)
            move(window, window.width // 2, window.height // 2)
            ui.preferences().allow_local_cli = False   # Narrowing always stands.
            REPORT["phase"] = "toggle_open"
        elif phase == "toggle_open":
            if not after(phase, 0.5):
                return 0.1
            open_toggle(window, area, region)
            REPORT["reports_before"] = len(refusal_reports())
            REPORT["phase"] = "toggle_soon"
        elif phase == "toggle_soon":
            if not soon_case("toggle", window, toggle_box, lambda: open_toggle(window, area, region)):
                return 0.01
            REPORT["phase"] = "toggle_soon_check"
        elif phase == "toggle_soon_check":
            if not after(phase, 1.0):
                return 0.1
            check("a_soon_press_on_a_guarded_checkbox_changes_nothing", ui.preferences().allow_local_cli is False)
            check("a_soon_press_on_a_guarded_checkbox_shows_the_hint", hinted(REPORT["soon"]["toggle"]["pressed_at"]))
            check("a_soon_press_on_a_guarded_checkbox_shows_no_error",
                  len(refusal_reports()) == REPORT["reports_before"], refusal_reports())
            if not toggle_box(window):
                open_toggle(window, area, region)
            REPORT["phase"] = "toggle_person"
        elif phase == "toggle_person":
            if not until(lambda: toggle_box(window) and after(phase, ARM), "the checkbox for the person"):
                return 0.1
            qa_input.press(window, lambda: toggle_box(window))   # A person's move onto it, then the press.
            REPORT["phase"] = "toggle_person_check"
        elif phase == "toggle_person_check":
            if not until(lambda: ui.preferences().allow_local_cli is True, "the person's press turns it on", 8):
                return 0.1
            check("the_persons_press_on_a_guarded_checkbox_changes_it", True)
            ui.preferences().allow_local_cli = False
            for value in ('PRESS', 'RELEASE'):
                window.event_simulate(type='ESC', value=value)
            return finish()
        return 0.1
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "fast-press.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_FAST_PRESS", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"],
                                           "checks": REPORT["checks"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=1.0)
