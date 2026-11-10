# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Waiting for the person is the card, never a status: the real window, real clicks, the app's own tool pump.

Found on 1.0.9 (Mac 2 dogfood, a GPT build turn in Auto): an expired web-export card, then every answered card, left
"Waiting for your approval" on the live row with no card anywhere. Real default startup; the chat connects through
the stand-in ``claude`` CLI (``fake_provider_cli.py``), then the suite asks for steps exactly as a provider's MCP call
does (``Controller.request_tool``) while a turn is marked running, and the app's own pump (``ui.pump``) raises,
expires and runs them. Every string the chat canvas draws is recorded where it is drawn (``chat_view.text``), with a
window screenshot at each moment checked. No account, network or inference.

Covered, in Auto unless said:

1. A file write's card names itself and says why it asks ("A text file can hold anything, ..."); while it is in
   view nothing else says the chat waits (the card replaces the live row). In Japanese the line is translated.
2. Declined by a real click: the card goes and nothing drawn, no status and no rail label says it waits.
3. Expired unanswered (1.0.9's web-export card): the same.
4. Approved in Ask (a scene program creating a cube; Ask shows no Auto line): it runs, and the same.
5. Scrolled into history: a pill at the transcript's top names the card; its click brings the buttons into view; a
   double-click's second press on the pill's spot is taken by the shield (an inert hit), answers nothing and shows
   no hover or hand, although a reply's link lies under it; once the shield lapses the spot is no hit at all: the
   hover, the hand and a press reach the link (released elsewhere, so nothing opens); Decline then works.

Run through ``run_native_suites.py --gui --include test_blender_approval_wait``; check ``approval-wait-gui.json``
for ``success`` and ``phase == "complete"``.
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
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-approval-wait-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup, as a person gets it.
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "approval-wait-gui.json"
WAITING = "Waiting for your approval"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "drawn": {}, "replies": [],
         "fixture": "Stand-in claude CLI for the connection; steps requested as a provider's MCP call does; real "
                    "clicks (event_simulate); the app's own tool pump; no account, network or inference"}
started = time.monotonic()
drawn_now = []


def record_text(original):
    def text(value, *args, **kwargs):
        drawn_now.append(str(value))
        return original(value, *args, **kwargs)
    return text


def install_recorder():
    from angelo_agent import chat_view
    if not getattr(chat_view.text, "_approval_wait_recorder", False):
        chat_view.text = record_text(chat_view.text)
        chat_view.text._approval_wait_recorder = True


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def hits():
    return list(view_state().get("hits", []))


def actions():
    return [(action, value) for _, action, value in hits()]


def centre(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in hits() if a == action and (value is None or v == value))
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def click_at(x, y):
    """A real click."""
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def click(action, value=None):
    click_at(*centre(action, value))


def press_at(x, y):
    """A press without its release (the release goes elsewhere, ``release_away``): what took it is read in between."""
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)


def release_away():
    """Release off every control (a release off the pressed control does nothing): a link under the spot never opens."""
    area, region = chat()
    x, y = region.x + 4, region.y + region.height - 4
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)   # The release carries its own position.


def compact(text):
    """Text without whitespace: a wrapped row breaks Japanese anywhere, so rows joined by spaces never match."""
    return "".join(str(text).split())


def capture(name):
    """Redraw the window, keep every string the chat drew in that redraw, and take a screenshot."""
    drawn_now.clear()
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        state["drawn"][name] = list(drawn_now)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return " ".join(" ".join(state["drawn"][name]).split())


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if state.get("finished"):
        return
    state["finished"] = True
    try:
        from angelo_agent import ui
        controller = ui.get_controller()
        controller.cancel_tools("Approval-wait GUI fixture ended", persist=False)
        controller.busy = False
    except Exception:   # noqa: BLE001  The report still gets written.
        state["cleanup_error"] = traceback.format_exc()
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_APPROVAL_WAIT", json.dumps({"success": state["success"], "phase": state["phase"],
          "failed": [name for name, ok in state["checks"].items() if not ok]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def check(name, condition, detail=None):
    state["checks"][name] = bool(condition)
    if detail is not None and not condition:
        state.setdefault("details", {})[name] = detail


def ask_for(controller, label, name, arguments):
    """A step requested as a provider's MCP call requests it, inside a running turn; the app's pump takes it on."""
    controller.busy, controller.cancelled = True, False
    controller.request_tool(label, name, arguments, state["replies"].append)
    chat()[0].tag_redraw()
    return controller.state["id"] + "/" + label


def nothing_says_it_waits(prefix, controller, shown):
    from angelo_agent import approval_wait, live_progress
    phase = live_progress.phase(live_progress.facts(controller))
    check(prefix + "_no_card_waits", approval_wait.awaiting(controller) is None)
    check(prefix + "_live_row_never_says_it_waits", "approval" not in phase.lower(), phase)
    check(prefix + "_no_status_claims_approval", "approval" not in str(controller.status).lower(), controller.status)
    check(prefix + "_no_rail_label", approval_wait.label(controller) == "")
    check(prefix + "_nothing_drawn_says_it_waits", WAITING not in shown and "Approval required" not in shown, shown)
    check(prefix + "_the_turn_still_shows_as_running", controller.busy and phase != "", phase)


WRITE = {"path": "notes.txt", "content": "Waiting-status fixture: never written (declined or expired)."}
PROGRAM = {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [],
           "operations": [{"kind": "create_primitive", "target": None,
                           "parameters": {"primitive": "CUBE", "alias": "aw_cube", "name": "Approval Cube",
                                          "size": 1}}]}


def tick():
    try:
        assert time.monotonic() - started < 240, "Approval-wait fixture timed out in " + state["phase"]
        from angelo_agent import approval_wait, auto_reasons, canvas_controls, chat_view, director, quick_connect, ui
        from angelo_agent.ui_text import iface_
        phase = state["phase"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running
                        and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            install_recorder()
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                        and ui.connection_available()):
                return .2
            check("a_new_chat_is_in_auto", controller.state["options"]["approval"] == "AUTO_BOUNDED",
                  controller.state["options"])
            state["write"] = ask_for(controller, "aw-write-1", "angelo_write_file", WRITE)
            state["phase"] = "write_card"
            return .3
        if phase == "write_card":
            if not wait(lambda: ("decline", state["write"]) in actions()):
                return .2
            call = controller.pending[state["write"]]
            shown = capture("aw-1-auto-card-says-why.png")
            reason = iface_(auto_reasons.LINES[auto_reasons.WRITE_FILE])
            check("the_card_is_the_one_awaited", approval_wait.awaiting(controller) is call)
            check("the_rules_code_picks_the_line", approval_wait.auto_reason(controller, call) == (
                auto_reasons.LINES[auto_reasons.WRITE_FILE], auto_reasons.line_fields(auto_reasons.WRITE_FILE)))
            check("the_card_says_why_it_asks_in_auto", compact(reason) in compact(shown), shown)
            check("in_view_nothing_else_says_it_waits", WAITING not in shown, shown)
            check("the_status_never_claims_the_card", "approval" not in str(controller.status).lower(),
                  controller.status)
            check("the_rail_label_names_the_card", approval_wait.label(controller) == "%s: %s" % (
                WAITING, approval_wait.title(call)), approval_wait.label(controller))
            view = bpy.context.preferences.view
            languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
            if bpy.app.build_options.international and "ja_JP" in languages:
                view.language, view.use_translate_interface = "ja_JP", True
                chat()[0].tag_redraw()
                state["phase"] = "write_card_japanese"
                return .5
            state["translation_runtime"] = "not available in this build"
            state["phase"] = "decline"
            return .1
        if phase == "write_card_japanese":
            shown = capture("aw-1b-auto-card-japanese.png")
            reason = iface_(auto_reasons.LINES[auto_reasons.WRITE_FILE])
            check("the_line_is_translated_at_display_time",
                  reason != auto_reasons.LINES[auto_reasons.WRITE_FILE] and compact(reason) in compact(shown), shown)
            bpy.context.preferences.view.language = "en_US"
            chat()[0].tag_redraw()
            state["phase"] = "decline"
            return .5
        if phase == "decline":
            if not wait(lambda: ("decline", state["write"]) in actions()):
                return .2
            click("decline", state["write"])
            state["phase"] = "declined"
            return .4
        if phase == "declined":
            if not wait(lambda: state["write"] not in controller.pending):
                return .2
            nothing_says_it_waits("declined", controller, capture("aw-2-declined.png"))
            check("declined_reply_reached_the_provider", any(isinstance(r, dict) and r.get("error_code") == "DECLINED"
                                                             for r in state["replies"]))
            state["expire"] = ask_for(controller, "aw-write-2", "angelo_write_file", WRITE)
            state["phase"] = "expire"
            return .3
        if phase == "expire":
            if not wait(lambda: ("decline", state["expire"]) in actions()):
                return .2
            controller.pending[state["expire"]].expires = time.monotonic() - 1   # Nobody answered in time.
            state["phase"] = "expired"
            return .3
        if phase == "expired":
            if not wait(lambda: state["expire"] not in controller.pending):
                return .2
            nothing_says_it_waits("expired", controller, capture("aw-3-expired.png"))
            check("expired_reply_reached_the_provider", any(isinstance(r, dict) and r.get("error_code") == "EXPIRED"
                                                            for r in state["replies"]))
            controller.set_options(approval="ASK")
            state["program"] = ask_for(controller, "aw-program", "angelo_scene_program", PROGRAM)
            state["phase"], state["approve_clicks"] = "program_card", 0
            return .3
        if phase == "program_card":
            if not wait(lambda: ("approve", state["program"]) in actions()):
                return .2
            shown = capture("aw-4-ask-card.png")
            call = controller.pending[state["program"]]
            check("ask_shows_no_auto_line", approval_wait.auto_reason(controller, call) is None and compact(iface_(
                auto_reasons.LINES[auto_reasons.WRITE_FILE])) not in compact(shown))
            state["phase"] = "approve"
            return .1
        if phase == "approve":
            if state["program"] not in controller.pending:
                state["phase"] = "approved"
                return .3
            assert state["approve_clicks"] < 3, "Approve clicked three times; the program still waits"
            if ("approve", state["program"]) in actions():
                click("approve", state["program"])   # A card whose first click shows its details takes a second.
                state["approve_clicks"] += 1
            return 1.0
        if phase == "approved":
            if not wait(lambda: "Approval Cube" in bpy.data.objects and not controller.pending):
                return .2
            state.pop("waiting", None)
            nothing_says_it_waits("approved", controller, capture("aw-5-approved-and-ran.png"))
            check("the_approved_step_ran", "Approval Cube" in bpy.data.objects)
            controller.set_options(approval="AUTO_BOUNDED")
            controller.state["messages"].append({"role": "user", "text": "History"})
            # History above the card: scrolled to its top, the card is out of view. Its reply is one link wrapping over
            # more rows than any transcript shows, in rows with no gap between them, so whatever row the revealed card
            # leaves at the transcript's top, a control lies under the pill's spot (gaps between replies or paragraphs,
            # or a right-aligned prompt, would leave the spot empty at some window sizes).
            words = " ".join("reference notes for history row %d," % index for index in range(400))
            controller.state["messages"].append({"role": "assistant",
                                                 "text": "[%s](https://example.invalid/history)" % words})
            controller.version += 1
            state["far"] = ask_for(controller, "aw-write-3", "angelo_write_file", WRITE)
            state["phase"] = "far_card"
            return .3
        if phase == "far_card":
            if not wait(lambda: ("decline", state["far"]) in actions()):
                return .2
            from angelo_agent.chat_history import scroll_key
            chat_view._scroll[scroll_key(chat()[0], controller.state["id"])] = 10 ** 6   # Scrolled up into history.
            chat()[0].tag_redraw()
            state["phase"] = "pill"
            return .4
        if phase == "pill":
            if not wait(lambda: ("approval_reveal", state["far"]) in actions()):
                chat()[0].tag_redraw()
                return .2
            shown = capture("aw-6-pill-names-the-card.png")
            call = controller.pending[state["far"]]
            pill = view_state().get("approval_pointer")
            check("the_pill_names_the_card", pill and pill[0] == call.id and pill[1] == "%s: %s" % (
                WAITING, approval_wait.title(call)), pill)
            check("the_pill_is_drawn", any(text.startswith(WAITING) for text in state["drawn"][
                "aw-6-pill-names-the-card.png"]), shown)   # Elided to the chat's width, never at its start.
            check("its_buttons_are_out_of_view", ("decline", state["far"]) not in actions())
            state["spot"] = centre("approval_reveal", state["far"])
            click_at(*state["spot"])
            state["phase"] = "revealed"
            return .1
        if phase == "revealed":
            if not wait(lambda: ("decline", state["far"]) in actions(), 5):
                return .05
            x, y = state["spot"][0] - chat()[1].x, state["spot"][1] - chat()[1].y   # Region pixels, as hits are.
            at_spot = [hit for hit in hits() if hit[0][0] <= x <= hit[0][0] + hit[0][2]
                       and hit[0][1] <= y <= hit[0][1] + hit[0][3]]
            shield = view_state().get("approval_shield")
            check("the_click_brought_the_buttons_into_view", ("approve", state["far"]) in actions())
            check("the_pills_spot_takes_the_shield_first", bool(at_spot) and at_spot[0][1] == canvas_controls.INERT,
                  at_spot)
            check("the_shield_is_live_at_the_second_press", shield and shield[1] and shield[1] > time.monotonic(),
                  "the redraw outlasted the shield" if shield else "no shield")
            state["under_shield"] = [(hit[1], hit[2]) for hit in at_spot if hit[1] != canvas_controls.INERT]
            check("a_control_lies_under_the_spot", bool(state["under_shield"]), at_spot)
            # A double-click's second press: Blender gives modal handlers a double-click as PRESS
            # (wm_event_modalkeymap_begin), and event_simulate never makes one.
            press_at(*state["spot"])
            state["phase"] = "second_press"
            return .15
        if phase == "second_press":
            pressed, hover, cursor = (view_state().get(key) for key in ("pressed", "hover", "cursor"))
            shown = capture("aw-7-second-press-taken-by-the-shield.png")
            check("the_shield_took_the_second_press", pressed is not None and pressed[0] == canvas_controls.INERT,
                  pressed)
            check("the_shield_shows_no_hover_or_hand", hover is None and cursor is None, (hover, cursor))
            check("the_revealed_card_needs_nothing_else_to_say_it_waits", WAITING not in shown, shown)
            release_away()
            state["phase"] = "after_second"
            return .3
        if phase == "after_second":
            call = controller.pending.get(state["far"])
            check("a_double_click_answers_nothing", call is not None and call.approval and not call.approved
                  and not call.executing, vars(call) if call else None)
            deadline = (view_state().get("approval_shield") or (None, time.monotonic()))[1] or time.monotonic()
            state["phase"] = "lapse"
            return max(.1, deadline - time.monotonic() + .3)
        if phase == "lapse":
            # Lapsed: the spot is no hit at all, for hover and press alike, even while no redraw has dropped it yet.
            state["stale_shield_still_listed"] = any(hit[1] == canvas_controls.INERT for hit in hits())
            press_at(*state["spot"])
            state["phase"] = "lapsed_press"
            return .15
        if phase == "lapsed_press":
            pressed, hover, cursor = (view_state().get(key) for key in ("pressed", "hover", "cursor"))
            check("after_the_shield_the_hover_is_what_lies_under", hover in state["under_shield"] and cursor ==
                  'HAND_POINT', (hover, cursor, state["under_shield"]))
            check("after_the_shield_a_press_reaches_what_lies_under", pressed in state["under_shield"],
                  (pressed, state["under_shield"]))
            release_away()   # Never over the link: nothing opens.
            state["phase"] = "decline_far"
            return .3
        if phase == "decline_far":
            if not wait(lambda: ("decline", state["far"]) in actions()):
                return .2
            capture("aw-8-after-the-shield.png")
            click("decline", state["far"])
            state["phase"] = "declined_far"
            return .4
        if phase == "declined_far":
            if not wait(lambda: state["far"] not in controller.pending):
                return .2
            nothing_says_it_waits("revealed_then_declined", controller, capture("aw-9-declined-after-reveal.png"))
            check("no_pill_without_a_card", ("approval_reveal", state["far"]) not in actions())
            check("revealed_card_declined_by_its_own_button",
                  sum(r.get("error_code") == "DECLINED" for r in state["replies"] if isinstance(r, dict)) == 2)
            state["phase"] = "done"
            return .1
        if phase == "done":
            check("drawn_text_recorded", all(state["drawn"].values()))
            finish()
            return None
    except Exception as error:   # noqa: BLE001
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
