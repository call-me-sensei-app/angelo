# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Blender menu over a chat card's armed Approve never lends Approve a click (the review of cf980b8..f6b01a9,
SHOULD 3; the maintainer: "Prove + fix in 2.1.0").

The grant guard's consent regions are window rects in C; a menu, popover, dialog, pie, search box or tooltip drawn
over one took no part in it: a move over the menu aimed at Approve beneath, a click on the menu's item minted
Approve's ticket, and the double-click's second press, the menu gone, approved a step the person never saw a button
for. Real default startup as test_blender_approval_wait: the chat connects through the stand-in ``claude`` CLI, a
scene program is requested in Ask as a provider's MCP call requests it, and the app's own pump raises its card.

1. The person's move arms Approve (aimed, its shield passed).
2. A menu of several rows opens at the pointer, over Approve (the suite fails if none of its rows is over Approve).
3. A click on the menu's item, then the next click at the same spot without moving: the step stays pending and
   unapproved (C's consent region took no press under the menu, and started again when the menu went).
4. The person's own aimed click on Approve approves it, and the program runs.

Run through ``run_native_suites.py --gui --include test_blender_covered_approve``; check
``covered-approve-gui.json`` for ``success`` and ``phase == "complete"``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-covered-approve-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (aimed clicks: qa_input.aim)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup, as a person gets it.
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "covered-approve-gui.json"
COVER = "Cover"   # The covering menu's rows: "Cover 1" ...
ROWS = 7   # A menu opens near the pointer, not exactly under it: one of its rows lands over Approve.
PROGRAM = {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [],
           "operations": [{"kind": "create_primitive", "target": None,
                           "parameters": {"primitive": "CUBE", "alias": "ca_cube", "name": "Covered Cube", "size": 1}}]}
state = {"phase": "boot", "success": False, "checks": {}, "fixture": "Stand-in claude CLI; a step requested as a "
         "provider's MCP call does; real clicks (event_simulate); the app's own tool pump; no account or network"}
started = time.monotonic()


class ANGELO_OT_suite_cover_approve_noop(bpy.types.Operator):
    """The covering menu's item: does nothing (the suite's own)."""
    bl_idname = "angelo.suite_cover_approve_noop"
    bl_label = COVER

    def execute(self, _context):
        return {'FINISHED'}


bpy.utils.register_class(ANGELO_OT_suite_cover_approve_noop)


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


def approve_rect():
    """Approve's rect in window pixels (x, y, w, h), or None."""
    found = next((rect for rect, action, value in hits() if action == "approve" and value == state.get("program")),
                 None)
    return qa_input.chat_rect(chat()[1], found) if found else None


def approve_armed():
    from angelo_agent import approval_arming
    found = next((h for h in hits() if h[1] == "approve" and h[2] == state.get("program")), None)
    return bool(found) and approval_arming.view(view_state()).key_armed(tuple(found[1:]))


def cover_item(point=None):
    """The covering menu's row over ``point`` (window pixels), or None; without ``point``, any of its rows."""
    from _bpy import _angelo_jobs
    rows = [(x, y, w, h) for pointer, _block, label, _idname, x, y, w, h in _angelo_jobs.popup_buttons()
            if pointer == window().as_pointer() and w > 0 and label.startswith(COVER)]
    if point is None:
        return rows[0] if rows else None
    return next((r for r in rows if r[0] <= point[0] < r[0] + r[2] and r[1] <= point[1] < r[1] + r[3]), None)


def open_cover():
    """A menu of ``ROWS`` rows opened where the pointer is, as any menu opens near it."""
    area, region = chat()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        def draw(menu, _context):
            for index in range(ROWS):
                menu.layout.operator(ANGELO_OT_suite_cover_approve_noop.bl_idname, text="%s %d" % (COVER, index + 1))
        bpy.context.window_manager.popup_menu(draw, title="")


def click_spot(x, y):
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='LEFTMOUSE', value=value, x=x, y=y)


def ask_for(controller, label, name, arguments):
    """A step requested as a provider's MCP call requests it, inside a running turn; the app's pump takes it on."""
    controller.busy, controller.cancelled = True, False
    controller.request_tool(label, name, arguments, lambda *_a: None)
    chat()[0].tag_redraw()
    return controller.state["id"] + "/" + label


def check(name, condition, detail=None):
    state["checks"][name] = bool(condition)
    if detail is not None and not condition:
        state.setdefault("details", {})[name] = detail


def wait(condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def finish(error=None):
    if state.get("finished"):
        return
    state["finished"] = True
    try:
        from angelo_agent import ui
        controller = ui.get_controller()
        controller.cancel_tools("Covered-approve GUI fixture ended", persist=False)
        controller.busy = False
        bpy.utils.unregister_class(ANGELO_OT_suite_cover_approve_noop)
    except Exception:   # noqa: BLE001  The report still gets written.
        state["cleanup_error"] = traceback.format_exc()
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_COVERED_APPROVE", json.dumps({"success": state["success"], "phase": state["phase"],
          "failed": [name for name, ok in state["checks"].items() if not ok]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "Covered-approve fixture timed out in " + state["phase"]
        from angelo_agent import director, quick_connect, ui
        phase = state["phase"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running
                        and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            from angelo_agent import approval_arming
            found = next(h for h in hits() if h[1:] == ("welcome_connect", "claude_subscription"))
            qa_input.aim(window(), lambda: qa_input.chat_rect(chat()[1], found[0]),
                         ready=lambda: approval_arming.view(view_state()).key_armed(tuple(found[1:])))
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                        and ui.connection_available()):
                return .2
            controller.set_options(approval="ASK")   # Narrowing: any code may.
            state["program"] = ask_for(controller, "ca-program-1", "angelo_scene_program", PROGRAM)
            state["phase"] = "card"
            return .3
        if phase == "card":
            if not wait(lambda: approve_rect() is not None):
                return .2
            x, y, w, h = approve_rect()
            state["point"] = (int(x + w / 2), int(y + h / 2))
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=state["point"][0], y=int(y - 6))
            state["phase"], state["reached"] = "arm", time.monotonic()
            return .2
        if phase == "arm":   # The person's hand moves onto Approve and rests while its shield passes.
            if not state.get("onto"):
                window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=state["point"][0], y=state["point"][1])
                state["onto"] = True
                return .2
            if not wait(lambda: approve_armed(), 8):
                return .2
            check("approve_armed_before_the_menu", True)
            open_cover()
            state["phase"] = "first_click"
            return .3
        if phase == "first_click":
            if not wait(lambda: cover_item() is not None, 8):
                return .1
            x, y = state["point"]
            if cover_item((x, y)) is None:
                return finish("no row of the covering menu is over Approve: a row %r, Approve's point %r"
                              % (cover_item(), state["point"]))
            click_spot(x, y)   # The menu's item: the menu closes.
            state["phase"] = "second_click"
            return .45   # Past the double-click time, with no move between.
        if phase == "second_click":
            if not wait(lambda: cover_item() is None, 8):
                return .1
            click_spot(*state["point"])   # The person's next click, where they were, without moving.
            state["phase"], state["second"] = "refused", time.monotonic()
            return .3
        if phase == "refused":
            if time.monotonic() - state["second"] < 1.0:
                return .1
            call = controller.pending.get(state["program"])
            check("the_double_click_approves_nothing", call is not None and not call.approved and not call.executing,
                  {"pending": call is not None, "approved": getattr(call, "approved", None)})
            check("nothing_ran", "Covered Cube" not in bpy.data.objects)
            state["phase"], state["clicks"] = "approve", 0
            return .2
        if phase == "approve":   # The person's own aimed click (a card whose first click shows details takes two).
            call = controller.pending.get(state["program"])
            if call is None or call.approved or call.executing:
                state["phase"] = "approved"
                return .3
            assert state["clicks"] < 3, "Approve clicked three times; the program still waits"
            if ("approve", state["program"]) in actions():
                from angelo_agent import approval_arming
                found = next(h for h in hits() if h[1:] == ("approve", state["program"]))
                qa_input.aim(window(), lambda: qa_input.chat_rect(chat()[1], found[0]),
                             ready=lambda: approval_arming.view(view_state()).key_armed(tuple(found[1:])))
                state["clicks"] += 1
            return 1.0
        if phase == "approved":
            if not wait(lambda: "Covered Cube" in bpy.data.objects and not controller.pending):
                return .2
            check("the_persons_own_click_approves", True)
            return finish()
        return .2
    except Exception as error:   # noqa: BLE001  Written to the report.
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)   # An aimed click lands first.
