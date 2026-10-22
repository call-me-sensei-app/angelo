# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""LIVE: the usage meter on two short real turns of a Claude subscription. Bills the person's own plan.

Opt-in only: ANGELO_QA_LIVE=1 and ANGELO_QA_USAGE_CONNECTIONS naming a dedicated connection store (for example one
``eval_live_agent.isolated_connection`` made: one Claude subscription's metadata, no credentials) that is also the
app's ANGELO_CONNECTIONS_DIR. The suite refuses to run on any other store, so it can never use the developer's own
sign-in. Two read-only turns (no scene change): a one-word reply, then about 120 words, during which the live
token count (``usage_meter.turn_tokens``) is sampled ten times a second. Then the bar and the card are captured.
What it records (``chat-usage-live.json`` in ANGELO_QA_DIR): the per-turn samples, the receipts' usage, the chat's
ledger totals, the bar's parts and the plan windows Claude Code reported. Run a GUI Blender with
--factory-startup --python <this-file>.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy
import gpu

assert "--factory-startup" in sys.argv and not bpy.app.background and os.environ.get("ANGELO_QA_LIVE") == "1"
DEDICATED = os.environ.get("ANGELO_QA_USAGE_CONNECTIONS", "")
assert DEDICATED and Path(DEDICATED).resolve() == Path(os.environ.get("ANGELO_CONNECTIONS_DIR", "/nonexistent")).resolve(), \
    "Set ANGELO_QA_USAGE_CONNECTIONS to a dedicated store and make it the app's ANGELO_CONNECTIONS_DIR"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-usage-live"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import chat_usage, chat_view, console, ui, usage_meter, usage_windows

bpy.context.preferences.view.show_splash = False
MESSAGES = ("Reply with the single word OK. Do not use any tools or change the scene.",
            "Without using any tools or changing the scene, describe a stone bridge at dusk in about 120 words.")
state = {"phase": "boot", "started": time.monotonic(), "checks": {}, "turns": []}
window = None


def record():
    (OUT / "chat-usage-live.json").write_text(json.dumps(state, indent=2, default=str))


def chat():
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


capture_request = None


def capture_drawn():
    """The chat region's own pixels, read in its draw callback (an unfocused window has no screen capture)."""
    global capture_request
    if capture_request is None or bpy.context.area.as_pointer() != capture_request["area"]:
        return
    region = bpy.context.region
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, region.width, region.height, 4, 0, 'UBYTE')
    pixels.dimensions = region.width * region.height * 4
    capture_request.update(pixels=[value / 255 for value in pixels], width=region.width, height=region.height)


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_drawn, (), 'WINDOW', 'POST_PIXEL')


def screenshot(name):
    global capture_request
    area, region = chat()
    capture_request = {"area": area.as_pointer()}
    with bpy.context.temp_override(window=window, area=area, region=region):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    request, capture_request = capture_request, None
    if "pixels" not in request:
        state.setdefault("missing_captures", []).append(name)
        return
    image = bpy.data.images.new("Angelo usage live frame", width=request["width"], height=request["height"], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request["pixels"])
        image.filepath_raw = str(OUT / name)
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)


def view_state():
    return chat_view._views.get(chat()[0].as_pointer(), {})


def tick():
    global window
    try:
        if time.monotonic() - state["started"] > 600:
            raise RuntimeError("Live usage QA timed out")
        if state["phase"] == "boot":
            window = bpy.context.window_manager.windows[0]
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "connect"
            return 1.0
        controller = ui.get_controller()
        if state["phase"] == "connect":
            connection = next((r for r in ui.ready_connections() if r.get("mode") == "claude_subscription"), None)
            if connection is None:
                raise RuntimeError("No ready Claude subscription connection in ANGELO_CONNECTIONS_DIR")
            ui.create_session(connection_id=connection["id"])
            ui.prepare_controller()
            controller = ui.get_controller()
            state["connection_ready"] = ui.connection_available()
            if not state["connection_ready"]:
                raise RuntimeError("No ready Claude subscription connection in ANGELO_CONNECTIONS_DIR")
            state["phase"], state["index"] = "send", 0
            return 0.5
        if state["phase"] == "send":
            ui.prepare_controller()
            controller.send(MESSAGES[state["index"]])
            state["turns"].append({"samples": [], "sent": time.monotonic()})
            state["phase"] = "running"
            return 0.1
        if state["phase"] == "running":
            turn = state["turns"][-1]
            if controller.busy:
                value = usage_meter.turn_tokens(controller)
                if value is not None:
                    turn["samples"].append((round(time.monotonic() - turn["sent"], 2), value))
                    if len(turn["samples"]) % 10 == 1:
                        record()  # Kept as it goes: a later failure never loses what was measured.
                chat()[0].tag_redraw()
                return 0.1
            receipt = next((r for r in reversed(controller.telemetry.receipts)
                            if r.get("session_id") == controller.state["id"]), {})
            turn.update(status=controller.status, usage=receipt.get("usage"), route=receipt.get("route"))
            state["index"] += 1
            if state["index"] < len(MESSAGES):
                state["phase"] = "send"
                return 0.5
            state["phase"] = "inspect"
            return 0.5
        if state["phase"] == "inspect":
            ledger = usage_meter.UsageLedger.shared(controller.telemetry.directory)
            state["ledger_chat"] = ledger.chat(controller.state["id"])
            state["bar"] = chat_usage.bar_parts(controller)
            state["windows"] = usage_windows.current(usage_windows.connection_key(controller))
            state["card"] = chat_usage.card_content(controller, "chat")
            record()
            screenshot("live-1-bar.png")
            area, region = chat()
            with bpy.context.temp_override(window=window, area=area, region=region):
                chat_view.show_popover(bpy.context, "usage")
            screenshot("live-2-card.png")
            view_state().pop("popover", None)
            checks = state["checks"]
            long_turn = state["turns"][-1]
            values = [value for _, value in long_turn["samples"]]
            checks["both_turns_completed"] = all(t["status"] == "Completed" for t in state["turns"])
            checks["live_count_moved_during_the_long_turn"] = len(set(values)) >= 3
            final = (long_turn.get("usage") or {}).get("output_tokens")
            state["long_turn_final_output"] = final
            checks["ledger_has_both_turns"] = bool(state["ledger_chat"]) and sum(
                bucket["requests"] for bucket in state["ledger_chat"]["modes"].values()) == 2
            checks["plan_bar_has_no_dollars"] = state["bar"]["money"] is None and "$" not in json.dumps(state["bar"])
            checks["no_draw_errors"] = chat_view.last_error is None
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            record()
            print("ANGELO_CHAT_USAGE_LIVE_OK" if state["success"] else "ANGELO_CHAT_USAGE_LIVE_FAILED",
                  json.dumps(checks), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        record()
        return 0.1
    except Exception as error:
        try:
            state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
            print(state["traceback"], flush=True)
            record()
        finally:
            bpy.ops.wm.quit_blender()  # Always: a stuck window would hold the caller's lock.
        return None


bpy.app.timers.register(tick, first_interval=1)
