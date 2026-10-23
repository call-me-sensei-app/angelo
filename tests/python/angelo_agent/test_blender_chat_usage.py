# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The usage meter in a real chat editor: the sticky bar, its card, a plan's windows, Arabic, and its draw cost.

1.0.5: a fresh chat (nothing sent) on a stand-in plan, API-key and team connection draws the zero bar at once:
"0 tokens" with the plan's busiest window, or with "≈ $0.00" and the Estimate label (read from the drawn text runs).

No inference, paid request or network: receipts and plan windows are fixture data fed through the same
functions the controller and the runtimes' event handlers call (``usage_meter.record``,
``usage_windows.observe_claude``). Clicks go through the chat's real modal handler.

Run a GUI Blender with --factory-startup --python <this-file>. Images and ``chat-usage.json`` go to
ANGELO_QA_DIR. The report's ``draw_cost`` compares the chat's own draw time (``chat_view.draw_seconds``)
with the meter shown and hidden, alternating blocks on the same chat.
"""

import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy
import gpu

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-usage-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_usage, chat_view, console, ui, usage_meter, usage_windows
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False

state = {"phase": "boot", "checks": {}, "started": time.monotonic(),
         "fixture": "Native UI only; receipts and plan windows are test data, no provider calls"}
window = handler = capture_request = None
BLOCKS, FRAMES = 6, 40
# Diagnosis only (ANGELO_QA_USAGE_SKIP, comma-separated): leave out "capture", "plan" (and Arabic with it), "arabic",
# "cost" or "microbench", to find which phase a finding belongs to. A normal run skips nothing.
SKIP = set(filter(None, os.environ.get("ANGELO_QA_USAGE_SKIP", "").split(",")))
state["skipped"] = sorted(SKIP)


def receipt(mode, prompt, cached, write, output, model="claude-opus-5-5", ttl="5m"):
    return {"status": "completed", "phases": {"total_ms": 9000}, "counts": {"provider_turn_requests": 1},
            "route": {"mode": mode, "requested_model": model, "cache_ttl": ttl, "served_models": [model]},
            "usage": {"input_tokens": prompt, "cached_input_tokens": cached, "cache_write_input_tokens": write,
                      "output_tokens": output, "turns": 1, "requests": 3}}


def capture_drawn():
    request = capture_request
    if request is None or bpy.context.area.as_pointer() != request["area"]:
        return
    source = chat_view._views.get(request["area"], {})
    region = bpy.context.region
    rect = (0, 0, region.width, region.height) if request["full"] else source.get("popup_rect")
    if rect and (request["full"] or source.get("popover")):
        x, y, w, h = [int(value) for value in rect]
        pixels = gpu.state.active_framebuffer_get().read_color(x, y, w, h, 4, 0, 'UBYTE')
        pixels.dimensions = w * h * 4
        request.update(pixels=[value / 255 for value in pixels], width=w, height=h,
                       pixel_hash=hashlib.sha256(bytes(pixels)).hexdigest())


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_drawn, (), 'WINDOW', 'POST_PIXEL')


def record():
    (OUT / "chat-usage.json").write_text(json.dumps(state, indent=2, default=str))


def area_region():
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def redraw(iterations=2):
    area, region = area_region()
    with bpy.context.temp_override(window=window, area=area, region=region):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=iterations)
    assert chat_view.last_error is None, chat_view.last_error


def capture(name, full=False):
    global capture_request
    if "capture" in SKIP:
        return
    area, _ = area_region()
    capture_request = {"area": area.as_pointer(), "full": full}
    redraw()
    request, capture_request = capture_request, None
    assert "pixels" in request, "The chat's draw callback did not capture this frame"
    image = bpy.data.images.new("Angelo usage QA frame", width=request["width"], height=request["height"], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request.pop("pixels"))
        image.filepath_raw = str(OUT / name)
        image.file_format = 'PNG'
        image.save()
        state.setdefault("captures", {})[name] = request
    finally:
        bpy.data.images.remove(image)


def event(kind, value='PRESS', x=None, y=None):
    area, region = area_region()
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + (x or 10), mouse_y=region.y + (y or 10),
                           mouse_prev_y=region.y + (y or 10), ctrl=False, oskey=False, shift=False, alt=False, unicode="")
    with bpy.context.temp_override(window=window, area=area, region=region):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def source():
    return chat_view._views[area_region()[0].as_pointer()]


def click(action, value=None, popup=True):
    redraw()
    hits = source()["popup_hits" if popup else "hits"]
    rect = next(rect for rect, found, arg in hits if found == action and (value is None or arg == value))
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    event('LEFTMOUSE', 'PRESS', x, y)
    event('LEFTMOUSE', 'RELEASE', x, y)
    redraw()


def drawn_texts():
    """Redraw once and return every text run the chat drew."""
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        redraw(1)
    finally:
        chat_view.text = original
    return runs


# A fresh chat (nothing sent) on stand-in connections of each kind: (mode, connection id, plan window or None).
FRESH = (("claude_subscription", "fresh-plan", .34), ("claude_api", "fresh-api", None), ("claude_team", "fresh-team", None))


def draw_cost():
    """Chat draw time per frame with the bar shown (A) and hidden (B), alternating blocks on the same chat."""
    shown, original = {"A": [], "B": []}, chat_usage.bar_parts
    try:
        for block in range(BLOCKS * 2):
            arm = "AB"[block % 2]
            chat_usage.bar_parts = original if arm == "A" else (lambda controller, connected=False: None)
            redraw(3)  # Settle the arm (layout and font caches) before timing.
            for _ in range(FRAMES):
                redraw(1)
                shown[arm].append(chat_view.draw_seconds * 1000)
    finally:
        chat_usage.bar_parts = original
    return {arm: {"frames": len(values), "median_ms": round(statistics.median(values), 4),
                  "p95_ms": round(sorted(values)[int(len(values) * .95) - 1], 4)} for arm, values in shown.items()}


def microbench(controller):
    """The meter's own work per frame (cached label) and per finished request (ledger add), in microseconds."""
    started = time.perf_counter()
    for _ in range(2000):
        chat_usage.bar_parts(controller)
    label = (time.perf_counter() - started) / 2000 * 1e6
    ledger = usage_meter.UsageLedger(OUT / "bench-ledger")
    (OUT / "bench-ledger").mkdir(exist_ok=True)
    value = receipt("claude_api", 120000, 100000, 8000, 900)
    started = time.perf_counter()
    for index in range(2000):
        ledger.add("bench-" + str(index % 40), value)
    add = (time.perf_counter() - started) / 2000 * 1e6
    ledger.drain()
    return {"bar_parts_cached_us": round(label, 2), "ledger_add_us": round(add, 2)}


def tick():
    global window, handler
    try:
        if time.monotonic() - state["started"] > 120:
            raise RuntimeError("Usage meter QA timed out")
        checks = state["checks"]
        if state["phase"] == "boot":
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            window = bpy.context.window_manager.windows[0]
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "fresh"
            return 0.6
        if not qa_boot.started():
            return 0.05
        area, region = area_region()
        controller = ui.get_controller()
        if state["phase"] == "fresh":
            # 1.0.5: the bar waited for the first request, so a new chat's header was empty and the plan's window was
            # hidden exactly when the person decides whether to send. With a connection selected it shows at once.
            fresh = state["fresh"] = {}
            for mode, connection, used in FRESH:
                controller.state.update(provider=mode, connection_id=connection, messages=[])
                controller.state.pop("next_route", None)
                if used is not None:
                    usage_windows.observe_claude(connection, {"rateLimitType": "five_hour", "utilization": used,
                                                              "resetsAt": time.time() + 3600})
                controller.version += 1
                with bpy.context.temp_override(window=window, area=area, region=region):
                    console.render(bpy.context, force=True)
                redraw()
                runs = drawn_texts()
                fresh[mode] = {"bar": source().get("usage_bar"), "anchor": source().get("usage_anchor"),
                               "drawn": [run for run in runs if "token" in run or "$" in run or "limit" in run
                                         or run in ("Estimate", "Included in your Claude plan")]}
                capture("usage-0-fresh-%s.png" % mode.split("_")[-1], full=True)
                wanted = (["0 tokens", "5-hour limit: 34% used"] if used is not None else ["0 tokens", "≈ $0.00", "Estimate"])
                checks["fresh_chat_bar_on_%s" % mode.split("_")[-1]] = (
                    source().get("usage_bar") == "0 tokens" and bool(source().get("usage_anchor"))
                    and all(text in runs for text in wanted) and (used is None or not any("$" in run for run in runs)))
            controller.state.update(provider="claude_api", connection_id=None, messages=[])
            controller.version += 1
            state["phase"] = "seed"
            return 0.2
        if state["phase"] == "seed":
            controller.state.update(provider="claude_api", connection_id=None)
            controller.state["messages"] = [
                {"role": "user", "text": "Make the bridge stone darker and add moss on the north side"},
                {"role": "assistant", "text": "Darkened the stone to a weathered grey and added a moss layer."},
                {"role": "user", "text": "Now light it for dusk"},
                {"role": "assistant", "text": "Low warm sun from the west, cooler sky fill, lamps on."}]
            controller.version += 1
            with bpy.context.temp_override(window=window, area=area, region=region):
                console.render(bpy.context, force=True)
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=area.as_pointer(), session=controller.state["id"],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            redraw()
            full_viewport = source()["message_viewport"]
            checks["no_bar_before_any_counted_request"] = chat_usage.bar_parts(controller) is None
            for value in (receipt("claude_api", 212000, 180000, 20000, 3100),
                          receipt("claude_api", 148000, 141000, 4000, 2200)):
                usage_meter.record(controller.telemetry.directory, controller.state["id"], value, "claude_api")
            redraw()
            parts = chat_usage.bar_parts(controller)
            state["api_bar"] = parts
            viewport = source()["message_viewport"]
            checks["transcript_stops_below_the_bar"] = (abs(full_viewport[1] + full_viewport[3] - chat_usage.BAR_HEIGHT
                                                            - viewport[1] - viewport[3]) < 1)
            checks["api_key_bar_shows_tokens_and_an_estimate"] = (source().get("usage_bar", "").endswith("tokens")
                                                                  and (parts["money"] or "").startswith("≈ $"))
            capture("usage-1-composer-api.png", full=True)
            click("usage", popup=False)
            checks["meter_opens_its_card"] = (source().get("popover") or {}).get("kind") == "usage"
            capture("usage-2-card-api.png")
            click("popup_usage_scope", "since")
            checks["scope_switches_to_since_start"] = source()["popover"].get("scope") == "since"
            capture("usage-3-card-since-start.png")
            event('ESC')
            redraw()
            checks["escape_closes_the_card"] = not source().get("popover")
            state["phase"] = "cost" if "plan" in SKIP else "plan"
            return 0.2
        if state["phase"] == "plan":
            ui.create_session("claude_subscription")  # A real second chat, on a plan.
            controller = ui.get_controller()
            controller.state["messages"] = [{"role": "user", "text": "Add a second lamp on the far side"},
                                            {"role": "assistant", "text": "Added a matching lamp on the far post."}]
            controller.version += 1
            with bpy.context.temp_override(window=window, area=area, region=region):
                console.render(bpy.context, force=True)
            handler.session = controller.state["id"]
            key = usage_windows.connection_key(controller)
            usage_meter.record(controller.telemetry.directory, controller.state["id"],
                               receipt("claude_subscription", 640000, 610000, 22000, 7400), "claude_subscription")
            now = time.time()
            usage_windows.observe_claude(key, {
                "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": .82,
                "resetsAt": int(now + 5400), "unifiedWindows": {
                    "five_hour": {"utilization": .82, "resetsAt": int(now + 5400)},
                    "seven_day": {"utilization": .12, "resetsAt": int(now + 4 * 86400)}}})
            redraw()
            parts = chat_usage.bar_parts(controller)
            state["plan_bar"] = parts
            checks["plan_bar_shows_its_window_and_no_dollars"] = (parts["money"] is None and parts["warn"]
                                                                  and parts["plan"] == "Included in your Claude plan"
                                                                  and "$" not in json.dumps(parts))
            capture("usage-4-composer-plan.png", full=True)
            click("usage", popup=False)
            capture("usage-5-card-plan.png")
            content = chat_usage.card_content(controller, "chat")
            checks["plan_card_has_windows_and_no_price"] = (len(content["windows"]) == 2
                                                           and "$" not in content["headline"])
            if "arabic" in SKIP:
                event('ESC')
                state["phase"] = "cost"
                return 0.3
            view = bpy.context.preferences.view
            languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
            state["arabic_available"] = bpy.app.build_options.international and "ar_EG" in languages
            if state["arabic_available"]:
                view.language, view.use_translate_interface = "ar_EG", True
            state["phase"] = "arabic"
            return 0.4
        if state["phase"] == "arabic":
            capture("usage-6-card-arabic.png", full=True)  # Mirrored: the card is drawn at the other edge.
            capture("usage-7-composer-arabic.png", full=True)
            bpy.context.preferences.view.language = "en_US"
            event('ESC')
            state["phase"] = "cost"
            return 0.3
        if state["phase"] == "cost":
            if "cost" not in SKIP:
                state["draw_cost"] = draw_cost()
            if "microbench" not in SKIP:
                state["microbench"] = microbench(controller)
            checks["no_draw_errors"] = chat_view.last_error is None
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            record()
            print("ANGELO_CHAT_USAGE_TEST_OK" if state["success"] else "ANGELO_CHAT_USAGE_TEST_FAILED",
                  json.dumps(checks), flush=True)
            bpy.types.SpaceConsole.draw_handler_remove(capture_handler, 'WINDOW')
            bpy.ops.wm.quit_blender()
            return None
        record()
        return 0.05
    except Exception as error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
        record()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
