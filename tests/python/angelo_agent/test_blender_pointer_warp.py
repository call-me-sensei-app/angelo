# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Code never aims the person's click (doc/angelo/grant_operators.rst, "Authority in C"; the independent review of
9c8b4e57077, its MUST).

A grant takes a ticket C minted from the person's press on a consent region. Were code able to put the pointer on a
grant control, the person's next click (meant for something else) would land there and grant. So:

* An agent script can't move the pointer, simulate input, or open a menu, panel or popup under it (``cursor_warp``,
  ``event_simulate``, ``wm.call_menu``, ``WindowManager.popup_menu``): each is refused with the grant's sentence.
* Wherever any code warps the pointer (an add-on, Blender's own warps), the region it lands in clicks nothing until
  the person moves out of it and back in: a twitch inside it and a press there grant nothing.
* The person's own move into the control, then a press, still grants (no false refusal).
* A menu an agent script opened (any opener: ``_angelo_jobs.menu_as_agent`` opens it as a C invoke would) mints
  nothing for a press on its grant item, even after it rebuilds unconfined; the same menu Angelo's own code opens
  does (the reviewer's read of d3024be, SHOULD 1).
* A confirm dialog Blender opens under the pointer takes the person's click there without a nudge: it runs in the
  consent the press that opened it held (the reviewer's read of 6392a6f, NIT e); a dialog left open across an agent
  turn does not, and needs the person's fresh press (the read of f5604750e18, (e)): an agent script's run, and an
  agent's typed tool (no script; the read of 927cd1a81a4).

The control is the composer's approval popover's "Approve for me" row, opened by the person's press. Synthetic
events in Angelo's default chat startup, isolated state; no provider, account, network or paid call. Run through
``run_native_suites.py --gui --include test_blender_pointer_warp``; ``pointer-warp.json`` has ``success`` and
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

assert not bpy.app.background, "The pointer suite needs a window"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The composer belongs to the default chat.
from angelo_agent import ui as _ui  # noqa: E402
_ui._authentication.start = lambda *args, **kwargs: None   # No sign-in: the composer shows as connected.
_ui.connection_available = lambda: True
_ui.prepare_controller = lambda: None
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-pointer-warp-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "phase": "boot", "checks": {}, "details": {}}
STARTED = time.monotonic()
LIMIT = 150.0 * qa_waits.timeout_scale()
SHIELD = 1.3   # Seconds a drawn region waits before a press on it counts (C's SHIELD_SECONDS, with room).
WAITING = {}
ATTEMPTS = {   # An agent script aiming the person's next click: each refused.
    "cursor_warp": "bpy.context.window.cursor_warp(40, 40)\n",
    "event_simulate": "bpy.context.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=40, y=40)\n",
    "call_menu": "bpy.ops.wm.call_menu(name='TOPBAR_MT_file')\n",
    # The Approval Policy menu, whose items grant: what any opener of it (a + menu row's operator) runs a script.
    "approval_menu": "bpy.ops.wm.call_menu(name='ANGELO_MT_approval')\n",
    "approval_menu_invoke": "bpy.ops.wm.call_menu('INVOKE_DEFAULT', name='ANGELO_MT_approval')\n",
    "popup_menu": "bpy.context.window_manager.popup_menu(lambda self, context: None, title='QA')\n",
    "popover": "bpy.context.window_manager.popover(lambda self, context: None)\n",
    "popup_menu_pie": "bpy.context.window_manager.popup_menu_pie(None, lambda self, context: None, title='QA')\n",
}
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


def again(*labels):
    """These phases run again: each ``after`` waits its seconds anew."""
    for label in labels:
        WAITING.pop("after:" + label, None)


def chat_parts():
    from angelo_agent import console, ui
    window = ui.main_window()
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def chat_view():
    from angelo_agent import chat_view as canvas
    return canvas._views.get(chat_parts()[1].as_pointer(), {})


def point(action, value=None, popup=False):
    """The window point at the middle of a drawn chat control (``qa_input.chat_point``: its scale, RTL mirrored)."""
    rect = next(r for r, a, v in chat_view().get("popup_hits" if popup else "hits", ())
                if a == action and (value is None or v == value))
    return qa_input.chat_point(chat_parts()[2], rect)


def auto_row():
    return point("popup_approval", "AUTO_BOUNDED", popup=True)


def approval_popover():
    return (chat_view().get("popover") or {}).get("kind") == "approval" and any(
        a == "popup_approval" and v == "AUTO_BOUNDED" for _r, a, v in chat_view().get("popup_hits", ()))


def typed_tool():
    """An agent's typed tool (no script) in the chat's window, as a turn runs it (``BlenderExecutor``): what it
    returned or raised. Whichever: an agent change was made or tried while the dialog was open."""
    from angelo_agent.blender_tools import BlenderExecutor
    window = chat_parts()[0]
    target = next((o.name for o in window.scene.objects), "Nothing")
    try:
        return str(BlenderExecutor(window).execute("angelo_set_transform", {"object": target, "location": [0, 0, 1]}))
    except Exception as error:   # noqa: BLE001 - a refusal is still the agent's call.
        return repr(error)


def confined(label, source):
    """``source`` run as an agent script (admitted, confined) in the chat's window: its refusal text, or None."""
    from angelo_agent import script_confinement
    from confinement_attacks_admit import admit
    window, area, region = chat_parts()
    admit("pointer-warp-" + label, source)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            script_confinement.run_confined(source, {"__name__": "__angelo_script__", "bpy": bpy}, None,
                                            "pointer-warp-" + label)
        return None
    except script_confinement.ScriptConfined as error:
        return script_confinement.reason_of(error)
    except Exception as error:   # noqa: BLE001 - the guard's own error is its refusal; anything else says so.
        return repr(error)


AUTO_ITEM = "Auto · bounded edits"   # ANGELO_MT_approval's item that widens the chat to Auto (chat_settings).


def menu_item(window, text):
    """The window point at the middle of the open menu's item ``text`` that runs angelo.set_approval, or None."""
    from _bpy import _angelo_jobs
    found = next(((x, y, w, h) for pointer, _block, label, idname, x, y, w, h in _angelo_jobs.popup_buttons()
                  if pointer == window.as_pointer() and label == text and w > 0
                  and idname in ("angelo.set_approval", qa_input.c_idname("angelo.set_approval"))), None)
    return (int(found[0] + found[2] / 2), int(found[1] + found[3] / 2)) if found else None


def trust_item(window):
    """The open Approval Policy menu's "Trust scripts for this chat…" item's middle, or None."""
    from _bpy import _angelo_jobs
    found = next(((x, y, w, h) for pointer, _block, _label, idname, x, y, w, h in _angelo_jobs.popup_buttons()
                  if pointer == window.as_pointer() and w > 0
                  and idname in ("angelo.trusted_scripts", qa_input.c_idname("angelo.trusted_scripts"))), None)
    return (int(found[0] + found[2] / 2), int(found[1] + found[3] / 2)) if found else None


def move(window, x, y):
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def click_here(window, x, y):
    """A press and release where the pointer already is, with no move first."""
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type='LEFTMOUSE', value=value, x=int(x), y=int(y))


def tick():
    from angelo_agent import console, grant_authority, grant_guard, ui
    try:
        assert time.monotonic() - STARTED < LIMIT, "Timed out in " + REPORT["phase"]
        phase = REPORT["phase"]
        controller = ui.get_controller()
        if phase == "boot" and not (ui.main_window() and qa_boot.started() and any(
                console.is_chat(a) for a in ui.main_window().screen.areas)
                and chat_view().get("hits")):
            return 0.2
        window, _area, _region = chat_parts()
        in_auto = lambda: grant_authority.chat_in_auto(controller.state["id"])   # noqa: E731
        if phase == "boot":
            controller.set_document(ui.document_key())
            controller.set_options(approval="ASK")
            qa_input.press(window, lambda: point("approval"))   # The person opens the approval popover.
            REPORT["phase"] = "popover"
        elif phase == "popover":
            if not until(lambda: approval_popover() and after("popover", SHIELD), "the approval popover"):
                return 0.1
            x, y = auto_row()
            window.cursor_warp(x, y)   # Code (not the person) puts the pointer on "Approve for me".
            move(window, x + 1, y)     # The person's hand twitches; the pointer never left the row.
            REPORT["at"] = (x + 1, y)
            REPORT["phase"] = "warped_press"
        elif phase == "warped_press":
            if not after("warped_press", SHIELD):
                return 0.1
            click_here(window, *REPORT["at"])   # Their click, meant for wherever they thought the pointer was.
            REPORT["phase"] = "warped_check"
        elif phase == "warped_check":
            if not after("warped_check", 1.0):
                return 0.1
            check("a_click_where_code_put_the_pointer_grants_nothing", not in_auto()
                  and controller.state["options"].get("approval") != "AUTO_BOUNDED")
            if not approval_popover():
                qa_input.press(window, lambda: point("approval"))   # Opened again by the person.
            REPORT["phase"] = "own_move"
        elif phase == "own_move":
            if not until(lambda: approval_popover() and after("own_move", SHIELD), "the popover again"):
                return 0.1
            x, y = auto_row()
            move(window, x, y - 120)   # The person's own move away from the row,
            qa_input.press(window, auto_row)   # then into it, and a press.
            REPORT["phase"] = "own_check"
        elif phase == "own_check":
            if not until(in_auto, "Auto by the person's own move and press"):
                return 0.1
            check("the_persons_own_move_in_and_press_grants", True)
            controller.set_options(approval="ASK")   # Narrowing: anything may choose it.
            REPORT["narrowed"] = not in_auto()
            from _bpy import _angelo_jobs
            with bpy.context.temp_override(window=window, area=_area, region=_region):   # As a C invoke would open it.
                _angelo_jobs.menu_as_agent("ANGELO_MT_approval")
            REPORT["phase"] = "agent_menu"
        elif phase == "agent_menu" and not REPORT.get("refreshed"):
            from _bpy import _angelo_jobs
            if not until(lambda: any(pointer == window.as_pointer() for pointer, *_ in _angelo_jobs.popup_buttons()),
                         "the menu the script opened"):
                return 0.1
            # Its items were built confined, so a grant's is inert; a change inside it or a window resize rebuilds
            # it later on the main thread, unconfined, where a grant's item is live again: the hole the tag closes.
            _angelo_jobs.refresh_popups()
            REPORT["refreshed"] = True
        elif phase in ("agent_menu", "own_menu"):
            if not until(lambda: menu_item(window, AUTO_ITEM) and after(phase, 0.5), "the Approval Policy menu"):
                return 0.1
            qa_input.press(window, lambda: menu_item(window, AUTO_ITEM))   # The person's real press on Auto.
            REPORT["phase"] += "_check"
        elif phase == "agent_menu_check":
            if not after(phase, 1.0):
                return 0.1
            check("a_press_in_a_menu_a_script_opened_grants_nothing", REPORT.get("narrowed") and not in_auto()
                  and controller.state["options"].get("approval") != "AUTO_BOUNDED", REPORT.get("narrowed"))
            for value in ('PRESS', 'RELEASE'):   # Whatever is left of that menu closes.
                window.event_simulate(type='ESC', value=value)
            REPORT["phase"] = "own_menu_open"
        elif phase == "own_menu_open":
            if not after(phase, 0.5):
                return 0.1
            with bpy.context.temp_override(window=window, area=_area, region=_region):   # Angelo's own code opens it.
                bpy.ops.wm.call_menu(name="ANGELO_MT_approval")
            REPORT["phase"] = "own_menu"
        elif phase == "own_menu_check":
            if not until(in_auto, "Auto from the menu Angelo opened"):
                return 0.1
            check("a_press_in_a_menu_angelo_opened_grants", True)
            move(window, window.width // 2, window.height // 2)   # The menu, then its dialog, open mid-window.
            REPORT["phase"] = "trust_menu"
        elif phase == "trust_menu":
            if not after(phase, 0.3):
                return 0.1
            with bpy.context.temp_override(window=window, area=_area, region=_region):
                bpy.ops.wm.call_menu(name="ANGELO_MT_approval")
            REPORT["phase"] = "trust_item"
        elif phase == "trust_item":
            if not until(lambda: trust_item(window) and after(phase, 0.5), "the Trust scripts item"):
                return 0.1
            qa_input.press(window, lambda: trust_item(window))   # The person's real press: the dialog opens.
            REPORT["phase"] = "trust_dialog"
        elif phase == "trust_dialog":
            from _bpy import _angelo_jobs
            if not until(lambda: _angelo_jobs.close_dialog_texts() and after(phase, 0.8), "the trust dialog"):
                return 0.1
            # Blender puts the dialog's confirm under the pointer (WM_POPUP_POSITION_MOUSE): the person clicks it
            # where the pointer already is, no nudge. It runs in the consent the item's press held for it.
            REPORT["details"]["trust_dialog"] = {"pointer": list(qa_input.sent[-1]), "buttons": [
                (label, idname, int(x), int(y), int(w), int(h)) for pointer, _block, label, idname, x, y, w, h
                in _angelo_jobs.popup_buttons() if pointer == window.as_pointer() and (label or idname)][:20]}
            at = qa_input.sent[-1]
            ok = next((b for b in REPORT["details"]["trust_dialog"]["buttons"] if b[0] == "OK"), None)
            assert ok and ok[2] <= at[0] <= ok[2] + ok[4] and ok[3] <= at[1] <= ok[3] + ok[5], \
                "setup: the dialog's OK is not under the pointer: %r" % REPORT["details"]["trust_dialog"]
            rounds = REPORT.setdefault("agent_rounds", [])
            if len(rounds) < 2:   # First an agent script, then an agent's typed tool, runs while the dialog is open.
                kind = ("script", "typed_tool")[len(rounds)]
                rounds.append(kind)
                REPORT["details"]["agent_ran_" + kind] = (confined("agent-turn", "x = 1\n") if kind == "script"
                                                          else typed_tool()[:300])
                click_here(window, *at)
                again("trust_after_agent")
                REPORT["phase"] = "trust_after_agent"
                return 0.1
            click_here(window, *at)
            REPORT["phase"] = "trust_check"
        elif phase == "trust_after_agent":
            if not after(phase, 1.0):
                return 0.1
            kind = REPORT["agent_rounds"][-1]
            check("a_dialog_left_open_across_an_agent_turn_needs_a_fresh_press" if kind == "script" else
                  "a_dialog_left_open_across_an_agents_typed_tool_needs_a_fresh_press", not controller.trusted_scripts,
                  REPORT["details"].get("agent_ran_" + kind))
            for value in ('PRESS', 'RELEASE'):   # Whatever is left of it closes.
                window.event_simulate(type='ESC', value=value)
            move(window, window.width // 2, window.height // 2)   # A key event carries no pointer: back mid-window.
            again("trust_menu", "trust_item", "trust_dialog")
            REPORT["phase"] = "trust_menu"
        elif phase == "trust_check":
            from _bpy import _angelo_jobs
            REPORT["details"]["trust_after"] = {"dialog": list(_angelo_jobs.close_dialog_texts())[:3],
                                                "trusted": controller.trusted_scripts}
            if not until(lambda: controller.trusted_scripts, "trust from the confirm under the pointer", 8):
                return 0.1
            check("the_dialogs_confirm_under_the_pointer_takes_a_click_without_a_nudge", True)
            sentence = grant_guard.SCRIPT_RUNS.split(": ")[-1].rstrip(".")   # Last: a menu one opened stays.
            for label, source in ATTEMPTS.items():
                said = confined(label, source)
                check("a_script_can_not_" + label, said is not None and sentence in said, said or "it ran")
            return finish()
        return 0.2
    except Exception:   # noqa: BLE001 - the report says what failed.
        return finish(traceback.format_exc())


def finish(error=None):
    REPORT.update(success=error is None and bool(REPORT["checks"]) and all(REPORT["checks"].values()),
                  phase="complete" if error is None else "failed", elapsed_seconds=round(time.monotonic() - STARTED, 2))
    if error:
        REPORT["error"] = error
    (OUT / "pointer-warp.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_POINTER_WARP", json.dumps({"success": REPORT["success"], "phase": REPORT["phase"],
                                             "checks": REPORT["checks"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=1.0)   # Never ahead of a press.
