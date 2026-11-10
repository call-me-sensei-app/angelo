# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hidden approval discovery and reveal-only input in a real isolated window.

Run only through run_native_suites.py --gui --include test_blender_hidden_wait.
Synthetic pending ToolCalls are inserted without a tool queue or provider. Actual
window events exercise the launcher, double-click and Return. Request replacement
and delayed-viewport cases exercise the production placement/input owners inside
Blender; the receipt distinguishes those checks from window-event checks. Window
screenshots still require independent pixel review. No account or inference.
"""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401

import json
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import qa_boot  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear("DISABLE_DEFAULT_CHAT")
os.environ["ANGELO_ENABLE_CHAT_LAUNCHER"] = "1"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-hidden-wait-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
REPORT = OUT / "hidden-wait.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "pixel_review": "REQUIRED_NOT_PERFORMED", "fixture": "Synthetic pending calls; no provider or tool queue",
         "case_kinds": {}, "observed": {}}
started = time.monotonic()
controllers, calls = [], []


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return (area, next(r for r in area.regions if r.type == "WINDOW")) if area else (None, None)


def canvas():
    from angelo_agent import chat_view
    area, _region = chat()
    return chat_view._views.get(area.as_pointer(), {}) if area else {}


def check(name, value, kind="native-contract"):
    state["checks"][name] = bool(value)
    state["case_kinds"][name] = kind
    assert value, name


def untouched():
    return all(not any(getattr(call, k, False) for k in ("authorized", "approved", "executing")) for call in calls)


def capture(name):
    path = OUT / (name + ".png")
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def events(x, y, double=False, key=None):
    win = window()
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=int(x), y=int(y))
    for _ in range(2 if double else 1):
        win.event_simulate(type=key or "LEFTMOUSE", value="PRESS", x=int(x), y=int(y))
        win.event_simulate(type=key or "LEFTMOUSE", value="RELEASE", x=int(x), y=int(y))


def wait(condition, seconds=15):
    if condition:
        state.pop("waiting_since", None)
        return True
    since = state.setdefault("waiting_since", time.monotonic())
    assert time.monotonic() - since < seconds, "Timed out in " + state["phase"]
    return False


def pending(controller, identity):
    from angelo_agent.pending_delivery import ToolCall
    call = ToolCall(identity, "angelo_execute_python", {"description": "Display-only fixture script", "code": "pass"},
                    controller.document, approval=True,
                    epoch=controller._epoch, generation=controller._request_generation)
    controller.pending[identity] = call
    calls.append(call)
    return call


def contracts(controller):
    """Production owners, inside Blender; these are explicitly not window-event claims."""
    from angelo_agent import approval_wait as aw, canvas_controls
    call = calls[0]
    identity = controller.state["id"]
    blocks = [("approval", None, [], 100, call.id, None)]
    actual = canvas()
    actual.update(hits=[((0, 0, 50, 20), "approve", call.id)], focus=("approve", call.id),
                  pressed=("approve", call.id), focused=True)
    aw.request_reveal(identity, call, controller=controller, shield=True)
    check("opening_drops_loaded_canvas_hits_focus_and_press", not actual.get("hits")
          and "focus" not in actual and "pressed" not in actual and not actual.get("focused"))
    aw.request_reveal(identity, call, controller=controller)
    check("unchanged_production_call_reveals", aw._place({}, controller, blocks, 17, 400) == 0)
    aw.request_reveal(identity, call, controller=controller, shield=True)
    replacement = pending(controller, call.id)
    placed = {"message_viewport": (0, 0, 300, 400)}
    check("same_id_new_call_does_not_inherit_scroll", aw._place(placed, controller, blocks, 17, 400) == 17)
    check("same_id_replacement_keeps_opening_shield", "approval_shield" in placed)
    controller.pending.pop(replacement.id, None)
    controller.pending[call.id] = call
    for target, field, changed in ((call, "epoch", call.epoch + 1), (call, "generation", call.generation + 1),
                                   (call, "delivery_owner", object()),
                                   (controller, "_epoch", controller._epoch + 1),
                                   (controller, "_request_generation", controller._request_generation + 1),
                                   (controller, "_worker_request", (999, 999))):
        exists, old = hasattr(target, field), getattr(target, field, None)
        aw.request_reveal(identity, call, controller=controller)
        setattr(target, field, changed)
        check("stale_fence_" + field, aw._place({}, controller, blocks, 17, 400) == 17)
        if exists:
            setattr(target, field, old)
        else:
            delattr(target, field)
    aw.request_reveal(identity, call, controller=controller, shield=True)
    delayed = {"session": identity, "hits": [((0, 0, 50, 20), "approve", call.id)],
               "focus": ("approve", call.id)}
    aw._place(delayed, controller, [], 17, 400)
    check("delayed_viewport_keeps_pending_shield", aw.pending_shield(identity))
    canvas_controls.press(lambda: check("pending_press_dispatched", False), delayed, delayed["hits"][0])
    check("pending_pointer_and_focus_cleared", not delayed["hits"] and "pressed" not in delayed)
    check("pending_return_rejected", not canvas_controls.key(SimpleNamespace(type="RET", value="PRESS"),
                                                              delayed, lambda: None))
    controller.pending.clear()
    aw._place(delayed, controller, [], 17, 400)
    check("gone_card_still_waits_for_viewport", aw.pending_shield(identity))
    delayed["message_viewport"] = (0, 0, 300, 400)
    aw._place(delayed, controller, [], 17, 400)
    check("gone_card_viewport_owns_shield", not aw.pending_shield(identity) and "approval_shield" in delayed)
    controller.pending[call.id] = call
    for flag in ("approved", "authorized", "automatic", "executing", "revoked"):
        old = getattr(call, flag)
        setattr(call, flag, True)
        check("not_unanswered_" + flag, not aw.unseen([controller], None, False))
        setattr(call, flag, old)
    check("contracts_never_authorized", untouched())


def finish(error=None):
    if error is not None:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = time.monotonic() - started
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_HIDDEN_WAIT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    for controller in controllers:
        controller.pending.clear()
    bpy.ops.wm.quit_blender()


def tick():
    try:
        from angelo_agent import approval_wait, chat_toggle, launcher_waiting, ui
        assert time.monotonic() - started < 100, "Fixture time bound"
        phase = state["phase"]
        if phase == "boot":
            if not qa_boot.started() or chat()[0] is None or not canvas().get("message_viewport"):
                return .2
            import angelo_agent
            check("exact_source_addon", Path(angelo_agent.__file__).resolve().parent ==
                  (ROOT / "scripts/addons_core/angelo_agent").resolve())
            state["observed"]["native_build_hash"] = bpy.app.build_hash.decode("ascii", "replace")
            state["observed"]["diagnostic_python_ownership_commit"] = "4daea5d11d05bc2d900e623acb959b90cd6f8bf2"
            a = ui.get_controller()
            a.state["messages"].append({"role": "user", "text": "Display-only hidden approval fixture"})
            a.state["title"] = "Hidden approval A"
            controllers.append(a)
            pending(a, "hidden-wait-a")
            contracts(a)
            b = ui.create_session()
            b.state["title"] = "Hidden approval B"
            b.state["messages"].append({"role": "user", "text": "Second display-only approval"})
            controllers.append(b)
            pending(b, "hidden-wait-b")
            ui.show_session(a.state["id"])
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.toggle_chat()
            state["phase"] = "hidden"
        elif phase == "hidden":
            launcher_waiting.refresh()
            rect = launcher_waiting._rects.get(window().as_pointer())
            if not wait(chat_toggle.chat_state(window(), bpy.context.window_manager.windows)[0] == "hidden" and rect):
                return .1
            check("hidden_active_card_discoverable", launcher_waiting.first()[0] == controllers[0].state["id"],
                  "window-draw")
            from angelo_agent import external_ui
            external_ui.refresh(ui)
            check("status_bar_tracks_hidden_local_card", external_ui._shown["local"][:2] ==
                  (controllers[0].state["id"], calls[0].id))
            capture("hidden-launcher-and-status")
            x, y, w, h = rect
            events(x + w / 2, y + h / 2, double=True)
            state["phase"] = "opened"
        elif phase == "opened":
            if not wait(chat()[0] is not None and canvas().get("message_viewport")):
                return .1
            check("launcher_double_click_reveal_only", untouched(), "window-event")
            capture("opened-approval")
            state["initial_viewport"] = canvas()["message_viewport"]
            chat()[0].spaces.active.history[-1].body = "Fixture composer line\n" * 7
            chat()[0].tag_redraw()
            state["phase"] = "composer_tall"
        elif phase == "composer_tall":
            viewport = canvas().get("message_viewport")
            if not wait(viewport and viewport[3] < state["initial_viewport"][3]):
                return .05
            state["small_viewport"] = viewport
            chat()[0].spaces.active.history[-1].body = ""
            current = controllers[0]
            approval_wait.request_reveal(current.state["id"], calls[0], controller=current, shield=True)
            chat()[0].tag_redraw()
            state["phase"] = "shield"
        elif phase == "shield":
            view = canvas()
            if not wait(view.get("approval_shield") and view["approval_shield"][1] is not None):
                return .02
            check("expanded_transcript_uses_current_frame_shield", view["message_viewport"][3] >
                  state["small_viewport"][3] and view["approval_shield"][0] == view["message_viewport"], "window-draw")
            check("shield_is_live_when_events_are_sent", time.monotonic() < view["approval_shield"][1], "window-draw")
            area, region = chat()
            rect = next((r for r, action, _ in view.get("hits", []) if action == "approve"), None)
            check("card_has_real_approve_hit", rect is not None, "window-draw")
            x, y, w, h = rect
            events(region.x + x + w / 2, region.y + y + h / 2, double=True)
            events(region.x + x + w / 2, region.y + y + h / 2, key="RET")
            state["events_sent"] = time.monotonic()
            state["phase"] = "shield_checked"
        elif phase == "shield_checked":
            if time.monotonic() - state["events_sent"] < .2:
                return .02
            check("actual_pointer_double_click_and_return_do_not_approve", untouched(), "window-event")
            check("original_call_still_pending", controllers[0].pending.get(calls[0].id) is calls[0], "window-event")
            capture("shielded-card")
            state["phase"] = "other_chat"
        elif phase == "other_chat":
            pointer = canvas().get("approval_pointer")
            # The current card is in view; removing it exposes the other conversation's pointer.
            controllers[0].pending.clear()
            chat()[0].tag_redraw()
            shield = canvas().get("approval_shield")
            shield_live = shield and (shield[1] is None or time.monotonic() < shield[1])
            if not wait(pointer and len(pointer) > 3 and pointer[3] == "approval_switch" and not shield_live):
                return .1
            check("other_chat_pointer_names_conversation", "Hidden approval B" in pointer[1], "window-draw")
            capture("other-chat-pointer")
            area, region = chat()
            rect = next(r for r, action, _ in canvas()["hits"] if action == "approval_switch")
            x, y, w, h = rect
            events(region.x + x + w / 2, region.y + y + h / 2, double=True)
            state["phase"] = "other_opened"
        elif phase == "other_opened":
            if not wait(ui.get_controller() is controllers[1] and canvas().get("session") == controllers[1].state["id"]):
                return .1
            check("other_chat_switch_is_reveal_only", untouched(), "window-event")
            check("other_chat_keeps_exact_pending_call", controllers[1].pending.get(calls[-1].id) is calls[-1],
                  "window-event")
            capture("other-chat-opened")
            controllers[1].pending.clear()
            launcher_waiting.refresh()
            check("no_unanswered_call_leaves_no_launcher_wait", launcher_waiting.first() is None)
            check("all_reveal_routes_never_authorized", untouched())
            finish()
            return None
        return .05
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.5)
