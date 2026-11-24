# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Grants made by the person's real presses, end to end, on the surfaces other suites reach through the harness
(doc/angelo/grant_operators.rst; the coordinator's 2.0.0 condition for suites that stand in for the person).

Each surface first refuses the same grant from a script (no press C saw), then takes it from presses sent through
the window (``qa_input``), each of which C mints a ticket for:

* Auto: the composer's approval control, then the popover's "Approve for me" row; C holds the chat in Auto.
* Review: a script's approval card's Review; C holds that call's exact text approved (the person read it).
* A new connection's key: the AI Providers page's edit control on a Google Images connection, the sheet's key field,
  a paste (the key from the clipboard) and Save; the key is the connection's.

Keys, as the coordinator's keyboard ruling keeps them (2026-11-16): inside a popover the person opened, or a field
they focused, a key grants; in one a script opened, or a field code focused, the same key is refused.

* The reasoning effort's Right arrow and End in the picker the person opened from the composer.
* Return on a model row the person moved to with the arrow keys.
* Return on the + menu's AI Render row (its card opens).
* Return in the AI Providers sheet's key field the person typed in (the key is saved).

Synthetic events in Angelo's default chat startup, isolated state; no provider, account, network or paid call. Run
through ``run_native_suites.py --gui --include test_blender_grant_surfaces``; ``grant-surfaces.json`` has
``success`` and ``phase == "complete"``.
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

assert not bpy.app.background, "The grant surfaces need a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
import qa_hit  # noqa: E402  (surface points to window pixels)
import qa_waits  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The composer belongs to the default chat.
from angelo_agent import ui as _ui  # noqa: E402
_ui._authentication.start = lambda *args, **kwargs: None   # No sign-in: the composer shows as connected.
_ui.connection_available = lambda: True
_ui.prepare_controller = lambda: None
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-grant-surfaces-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}}
STARTED = time.monotonic()
LIMIT = 280.0 * qa_waits.timeout_scale()
KEY = "AIza-qa-not-a-real-key-0123456789abcdef"
KEY2 = "AIza-qa-second-not-a-real-key-fedcba987654"
SHIELD = 1.3   # Seconds after focus arrives before a key on it counts (C's SHIELD_SECONDS, with room).
SCRIPT = {"description": "Name the scene QA Reviewed", "code": "bpy.context.scene['qa_reviewed'] = 1\nresult = 1"}
WAITING = {}
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False


def check(name, condition, detail=None):
    REPORT["checks"][name] = bool(condition)
    if not condition and detail is not None:
        REPORT["details"][name] = str(detail)[:1500]
    assert condition, name


def until(condition, label, limit=30.0):
    """``condition()`` once true, else None; a wait over ``limit`` x the scale fails, naming it."""
    value = condition()
    if value:
        WAITING.pop(label, None)
        return value
    since = WAITING.setdefault(label, time.monotonic())
    assert time.monotonic() - since < limit * qa_waits.timeout_scale(), "Timed out waiting: " + label
    return None


def after(label, seconds):
    """True once ``seconds`` have passed since this wait began."""
    return time.monotonic() - WAITING.setdefault("after:" + label, time.monotonic()) >= seconds


def models():
    from angelo_agent.chat_settings import normalize_models
    return normalize_models([
        {"model": identity, "displayName": name, "isDefault": i == 0,
         "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high", "xhigh", "max")],
         "defaultReasoningEffort": "medium"}
        for i, (identity, name) in enumerate((("qa-sol", "QA Sol"), ("qa-astra", "QA Astra"), ("qa-luna", "QA Luna")))])


def key(window, kind, x, y, **modifiers):
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=kind, value=value, x=int(x), y=int(y), **modifiers)


def chat_key(kind):
    """The person's key with the pointer over the chat (its modal takes it)."""
    window, _area, region = chat_parts()
    key(window, kind, region.x + region.width // 2, region.y + region.height // 2)


def prefs_key(kind, **modifiers):
    window, _area, region = prefs_parts()
    key(window, kind, region.x + region.width // 2, region.y + region.height // 2, **modifiers)


AI_RENDER = ("popup_add_open", "ai_render")   # The + menu's AI Render feature row (its card opens).


def step_to(row, label):
    """Down arrows until the + menu's keyboard focus is ``row``; True once it is. Fails after twelve rows."""
    from angelo_agent import chat_popovers
    if tuple(chat_popovers.key_focus(chat_view())) == row:
        return True
    steps = REPORT.setdefault("steps", {})
    if time.monotonic() - steps.get(label, (0, 0.0))[1] >= 0.3:
        count = steps.get(label, (0, 0.0))[0] + 1
        assert count <= 12, "the + menu never reached %r: %r" % (row, chat_popovers.key_focus(chat_view()))
        steps[label] = (count, time.monotonic())
        chat_key('DOWN_ARROW')
    return False


def popover():
    return chat_view().get("popover") or {}


def refused(call):
    """Whether ``call()`` (a script's grant) was refused by the grant guard."""
    from angelo_agent import grant_guard
    try:
        result = call()
    except (RuntimeError, PermissionError) as error:
        return grant_guard.NOT_YOURS in str(error) or grant_guard.SCRIPT_RUNS in str(error)
    return 'CANCELLED' in result


def chat_parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def chat_view():
    from angelo_agent import chat_view as canvas
    return canvas._views.get(chat_parts()[1].as_pointer(), {})


def chat_press(action, value=None, popup=False):
    """The person's press on a drawn chat control (canvas points -> window pixels: ``qa_input.chat_point``)."""

    def point():
        hits = chat_view().get("popup_hits" if popup else "hits", ())
        return qa_input.chat_point(chat_parts()[2], next(r for r, a, v in hits if a == action
                                                          and (value is None or v == value)))
    qa_input.press(chat_parts()[0], point)


def chat_aim(action, value=None):
    """The person's aimed press on a shielded chat control (a card's Review, as its Approve): the pointer waits just
    below it while C's shield passes, then moves into it and presses (``qa_input.aim``)."""

    def rect():
        found = next(r for r, a, v in chat_view().get("hits", ()) if a == action and (value is None or v == value))
        return qa_input.chat_rect(chat_parts()[2], found)
    qa_input.aim(chat_parts()[0], rect)


def prefs_parts():
    for window in bpy.context.window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == 'PREFERENCES'), None)
        if area is not None:
            return window, area, next(r for r in area.regions if r.type == 'WINDOW')
    return None, None, None


def page_row(action, value, label):
    """The AI Providers page's hits once control ``(action, value)`` is wholly in view (scrolling toward it), else
    None; ``label`` names the wait."""
    from angelo_agent import providers_page_host as page
    window, area, region = prefs_parts()
    plan = page.plan_for(region) if region else None
    rect = next((r for r, a, v in (plan.hits if plan else ()) if a == action and v == value), None)
    if rect is not None and 24 <= rect[1] and rect[1] + rect[3] <= region.height - 24:
        return plan.hits
    if until(lambda: False, label, 40) is None and rect is not None:   # The wait's bound, then a scroll step toward it.
        scroll = bpy.ops.view2d.scroll_down if rect[1] < 24 else bpy.ops.view2d.scroll_up
        with bpy.context.temp_override(window=window, area=area, region=region):
            scroll()
    return None


def page_press(hits, action, value=None):
    window, _area, region = prefs_parts()
    rect = next(r for r, a, v in hits if a == action and (value is None or v == value))
    qa_input.press(window, *qa_hit.point(qa_hit.PIXELS, region, rect))   # The page lays out in region pixels.


def tick():
    from angelo_agent import card_host, chat_add_nav, chat_popovers, console, grant_authority, ui
    from angelo_agent import chat_view as canvas, providers_page_host as page
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        controller = ui.get_controller()
        if phase == "boot" and not (ui.main_window() and qa_boot.started() and any(
                console.is_chat(a) for a in ui.main_window().screen.areas) and chat_view().get("hits")):
            return 0.2   # Angelo's start-up builds the chat first.
        window, area, region = chat_parts()
        if phase == "boot":
            controller.set_document(ui.document_key())
            controller.set_options(approval="ASK")   # Narrowing: anything may choose it.
            with bpy.context.temp_override(window=window, area=area, region=region):
                check("a_scripted_auto_is_refused", refused(lambda: bpy.ops.angelo.set_approval(mode='AUTO_BOUNDED'))
                      and not grant_authority.chat_in_auto(controller.state["id"]))
            chat_press("approval")
            REPORT["phase"] = "auto_popover"
        elif phase == "auto_popover":
            if not until(lambda: (chat_view().get("popover") or {}).get("kind") == "approval" and any(
                    a == "popup_approval" and v == "AUTO_BOUNDED" for _r, a, v in chat_view().get("popup_hits", ())),
                    "the approval popover"):
                return 0.2
            chat_press("popup_approval", "AUTO_BOUNDED", popup=True)
            REPORT["phase"] = "auto_set"
        elif phase == "auto_set":
            if not until(lambda: grant_authority.chat_in_auto(controller.state["id"]), "Auto held in C"):
                return 0.2
            check("the_persons_press_picks_auto", controller.state["options"].get("approval") == "AUTO_BOUNDED")
            controller.set_options(approval="ASK")
            replies = []
            controller.busy = True
            controller.request_tool("qa-review", "angelo_execute_python", dict(SCRIPT), replies.append)
            REPORT["phase"] = "review_card"
        elif phase == "review_card":
            call = next((c for c in controller.pending.values() if c.id.endswith("/qa-review")), None)
            if not until(lambda: call is not None and any(a == "review" and v == call.id
                                                          for _r, a, v in chat_view().get("hits", ())),
                         "the script card's Review"):
                return 0.2
            with bpy.context.temp_override(window=window, area=area, region=region):
                check("a_scripted_review_is_refused", refused(lambda: bpy.ops.angelo.review_tool(
                    call_id=call.id, session_id=controller.state["id"])) and call.id not in controller.reviewed_calls)
            REPORT["call"] = call.id
            chat_aim("review", call.id)
            REPORT["phase"] = "reviewed"
        elif phase == "reviewed":
            call = controller.pending.get(REPORT["call"])
            if not until(lambda: REPORT["call"] in controller.reviewed_calls, "the person's Review"):
                return 0.2
            check("the_persons_press_reviews_the_script", call is not None and call.reviewed_script)
            controller.decline_tool(REPORT["call"])
            controller.busy = False
            controller.models, controller.refresh_models = models(), lambda: None
            controller.set_options(model="qa-sol", effort="medium", fast=False)
            with bpy.context.temp_override(window=window, area=area, region=region):   # A script opens the picker.
                canvas.show_popover(bpy.context, "controls")
            REPORT["phase"] = "effort_code"
        elif phase == "effort_code":
            if not until(lambda: popover().get("kind") == "controls" and after("effort_code", SHIELD), "picker"):
                return 0.1
            chat_key('RIGHT_ARROW')
            REPORT["phase"] = "effort_code_check"
        elif phase == "effort_code_check":
            if not after("effort_code_check", 0.8):
                return 0.1
            check("an_arrow_in_a_picker_a_script_opened_is_refused", controller.state["options"]["effort"] == "medium")
            chat_key('ESC')
            REPORT["phase"] = "effort_open"
        elif phase == "effort_open":
            if not until(lambda: not popover(), "the script's picker closed"):
                return 0.1
            chat_press("model")   # The person opens the picker from the composer's model chip.
            REPORT["phase"] = "effort_key"
        elif phase == "effort_key":
            if not until(lambda: popover().get("kind") == "controls" and after("effort_key", SHIELD), "the picker"):
                return 0.1
            chat_key('RIGHT_ARROW')
            REPORT["phase"] = "effort_moved"
        elif phase == "effort_moved":
            if not until(lambda: controller.state["options"]["effort"] == "high", "the person's Right arrow"):
                return 0.1
            check("the_persons_arrow_changes_effort", True)
            chat_key('END')
            REPORT["phase"] = "effort_end"
        elif phase == "effort_end":
            if not until(lambda: controller.state["options"]["effort"] == "max", "the person's End"):
                return 0.1
            check("the_persons_end_sets_max_effort", True)
            chat_press("popup_models", popup=True)
            REPORT["phase"] = "model_list"
        elif phase == "model_list":
            if not until(lambda: popover().get("kind") == "models" and popover().get("rows"), "the model list"):
                return 0.1
            chat_key('DOWN_ARROW')
            REPORT["phase"] = "model_down"
        elif phase == "model_down":
            if not after("model_down", SHIELD):
                return 0.1
            rows, cursor = popover().get("rows") or [], popover().get("cursor", 0)
            REPORT["model_row"] = rows[cursor]["value"] if 0 <= cursor < len(rows) else None
            chat_key('RET')
            REPORT["phase"] = "model_set"
        elif phase == "model_set":
            row = REPORT.get("model_row")
            chosen = row[1] if isinstance(row, (list, tuple)) else row
            if not until(lambda: chosen and controller.state["options"]["model"] == chosen, "the person's Return"):
                return 0.1
            check("the_persons_return_picks_the_model_row", chosen != "qa-sol", REPORT.get("model_row"))
            with bpy.context.temp_override(window=window, area=area, region=region):   # A script opens the + menu.
                canvas.show_popover(bpy.context, "add")
            chat_add_nav.set_query(popover(), "AI Render")
            REPORT["phase"] = "plus_code"
        elif phase == "plus_code":
            if not (after("plus_code", 0.3) and step_to(AI_RENDER, "plus_code")):
                return 0.1
            REPORT["phase"] = "plus_code_ret"
        elif phase == "plus_code_ret":
            if not after("plus_code_ret", SHIELD):
                return 0.1
            chat_key('RET')
            REPORT["phase"] = "plus_code_check"
        elif phase == "plus_code_check":
            if not after("plus_code_check", 0.8):
                return 0.1
            check("return_in_a_menu_a_script_opened_is_refused", not card_host.is_active("ai_render"))
            chat_key('ESC')
            REPORT["phase"] = "plus_open"
        elif phase == "plus_open":
            if not until(lambda: not popover(), "the script's menu closed"):
                return 0.1
            chat_press("attach")   # The person opens the + menu.
            REPORT["phase"] = "plus_query"
        elif phase == "plus_query":
            if not until(lambda: popover().get("kind") == "add", "the + menu"):
                return 0.1
            chat_add_nav.set_query(popover(), "AI Render")
            REPORT["phase"] = "plus_step"
        elif phase == "plus_step":
            if not (after("plus_step", 0.3) and step_to(AI_RENDER, "plus_step")):
                return 0.1
            REPORT["phase"] = "plus_ret"
        elif phase == "plus_ret":
            if not after("plus_ret", SHIELD):
                return 0.1
            REPORT["details"]["plus_row"] = list(map(str, chat_popovers.key_focus(chat_view())))
            chat_key('RET')
            REPORT["phase"] = "plus_card"
        elif phase == "plus_card":
            if not until(lambda: card_host.is_active("ai_render"), "the person's Return on AI Render"):
                return 0.1
            check("the_persons_return_opens_the_rows_card", True)
            card_host.close()
            record = ui.connections().add("GOOGLE", "API_KEY", "QA Google Images", group="GENAI")
            REPORT["second"] = ui.connections().add("GOOGLE", "API_KEY", "QA Google Images 2", group="GENAI")["id"]
            REPORT["connection"] = record["id"]
            check("a_scripted_key_is_refused", refused(lambda: bpy.ops.angelo.new_connection(
                'EXEC_DEFAULT', connection_id=record["id"], group="GENAI", provider="GOOGLE", method="API_KEY",
                api_key=KEY)) and ui._memory_keys.get(record["id"]) != KEY)
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            REPORT["phase"] = "page"
        elif phase == "page":
            hits = page_row("edit", REPORT["connection"], "the connection's edit control in view")
            if hits is None:
                return 0.2
            page_press(hits, "edit", REPORT["connection"])
            REPORT["phase"] = "sheet"
        elif phase == "sheet":
            if not until(lambda: page._sheet is not None and page._sheet_plan is not None, "the connection sheet"):
                return 0.2
            page_press(page._sheet_plan.hits, "field", "key")
            REPORT["phase"] = "paste"
        elif phase == "paste":
            target = prefs_parts()[0]
            bpy.context.window_manager.clipboard = KEY
            for value in ('PRESS', 'RELEASE'):
                target.event_simulate(type='V', value=value, ctrl=value == 'PRESS', oskey=False,
                                      x=target.width // 2, y=target.height // 2)
            REPORT["phase"] = "save"
        elif phase == "save":
            if not until(lambda: page._sheet is not None and page._sheet.key.text == KEY, "the pasted key"):
                return 0.2
            page_press(page._sheet_plan.hits, "submit")
            REPORT["phase"] = "saved"
        elif phase == "saved":
            if not until(lambda: ui._memory_keys.get(REPORT["connection"]) == KEY, "the key saved by the person"):
                return 0.2
            check("the_persons_presses_save_the_key", ui._memory_keys.get(REPORT["connection"]) == KEY)
            page.close_sheet("done")   # The saved connection's sheet goes (its status check is not this suite's).
            REPORT["phase"] = "edit2"
        elif phase == "edit2":
            hits = page_row("edit", REPORT["second"], "the second connection's row") if page._sheet is None else None
            if hits is None:
                return 0.2
            page_press(hits, "edit", REPORT["second"])
            REPORT["phase"] = "sheet2"
        elif phase == "sheet2":
            if not until(lambda: page._sheet is not None and page._sheet_plan is not None, "the second sheet"):
                return 0.2
            page._sheet.key.editing, page._sheet.key.text = True, KEY2   # Focused and typed by code, not the person.
            prefs_key('RET')
            REPORT["phase"] = "sheet2_code"
        elif phase == "sheet2_code":
            if not after("sheet2_code", 1.0):
                return 0.1
            check("return_in_a_field_code_focused_is_refused", ui._memory_keys.get(REPORT["second"]) != KEY2)
            page._sheet.key.editing, page._sheet.key.text = False, ""
            page_press(page._sheet_plan.hits, "field", "key")   # The person's own press into the key field.
            REPORT["phase"] = "sheet2_paste"
        elif phase == "sheet2_paste":
            bpy.context.window_manager.clipboard = KEY2
            prefs_key('V', ctrl=True)
            REPORT["phase"] = "sheet2_ret"
        elif phase == "sheet2_ret":
            if not until(lambda: page._sheet is not None and page._sheet.key.text == KEY2 and after("sheet2", SHIELD),
                         "the second key pasted"):
                return 0.1
            prefs_key('RET')
            REPORT["phase"] = "sheet2_saved"
        elif phase == "sheet2_saved":
            if not until(lambda: ui._memory_keys.get(REPORT["second"]) == KEY2, "the person's Return saving the key"):
                return 0.2
            check("the_persons_return_in_the_key_field_saves_it", True)
            return finish()
        return 0.3
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "grant-surfaces.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_GRANT_SURFACES", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=1.0)   # Never ahead of a press.
