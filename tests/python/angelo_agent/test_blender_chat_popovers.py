# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated native GPU/modal picker QA. No inference, paid request or microphone.

Run a GUI Blender with --factory-startup --python <this-file>. Images and a
machine-readable report go to ANGELO_QA_DIR. Events exercise the real native
modal handler, not OS clicks in a user's Blender window.
"""

import copy
import hashlib
import json
import os
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-picker-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, chat_view, console
from angelo_agent.chat_popovers import model_rows
from angelo_agent.chat_settings import normalize_models

ui._authentication.start = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False

MODELS = normalize_models([
    {"model": identity, "displayName": name, "isDefault": i == 0,
     "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high", "xhigh", "max")],
     "defaultReasoningEffort": "medium", "serviceTiers": [{"id": "priority"}]}
    for i, (identity, name) in enumerate((
        ("gpt-6.1-sol", "GPT-6.1 Sol"), ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-6-sol", "GPT-6 Sol"), ("gpt-6-luna", "GPT-6 Luna"),
        ("gpt-5.6-sol", "GPT-5.6 Sol"), ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"), ("gpt-5.5", "GPT-5.5")))
])
state = {"phase": "boot", "checks": {}, "started": time.monotonic(), "fixture": "Native UI only; catalog is test data, no provider calls"}
window = None
handler = None
capture_request = None


def capture_drawn_popup():
    """Read the current editor GPU target, avoiding macOS front-buffer lag."""
    request = capture_request
    if request is None or bpy.context.area.as_pointer() != request["area"]:
        return
    source = chat_view._views.get(request["area"], {})
    rect = source.get("popup_rect")
    if rect and source.get("popover"):
        x, y, w, h = [int(value) for value in rect]
        # Registered after the real canvas callback; these are its current
        # pixels, not a mockup or a cropped/edited earlier screenshot.
        pixels = gpu.state.active_framebuffer_get().read_color(x, y, w, h, 4, 0, 'UBYTE')
        pixels.dimensions = w * h * 4
        request.update(pixels=[value / 255 for value in pixels], width=w, height=h,
                       pixel_hash=hashlib.sha256(bytes(pixels)).hexdigest(),
                       kind=source["popover"]["kind"], options=dict(ui.get_controller().state["options"]))


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_drawn_popup, (), 'WINDOW', 'POST_PIXEL')


def record():
    (OUT / "chat-popovers.json").write_text(json.dumps(state, indent=2, default=str))


def area_region():
    area = next(a for a in window.screen.areas if console.is_chat(a))
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return area, region


def redraw():
    area, region = area_region()
    with bpy.context.temp_override(window=window, area=area, region=region):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    assert chat_view.last_error is None, chat_view.last_error


def capture(name):
    global capture_request
    area, _ = area_region()
    capture_request = {"area": area.as_pointer()}
    redraw()
    request, capture_request = capture_request, None
    assert "pixels" in request, "The native popup draw callback did not capture this frame"
    image = bpy.data.images.new("Angelo UI QA frame", width=request["width"], height=request["height"], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request.pop("pixels"))
        image.filepath_raw = str(OUT / name)
        image.file_format = 'PNG'
        image.save()
        state.setdefault("captures", {})[name] = request
    finally:
        bpy.data.images.remove(image)


def event(kind, value='PRESS', x=None, y=None, unicode=""):
    area, region = area_region()
    x = region.width - 100 if x is None else x
    y = 40 if y is None else y
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + x, mouse_y=region.y + y,
                           mouse_prev_y=region.y + y, ctrl=False, oskey=False, shift=False, alt=False, unicode=unicode)
    with bpy.context.temp_override(window=window, area=area, region=region):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def click(action, value=None, popup=True, before_release=None):
    redraw()
    area, _ = area_region()
    source = chat_view._views[area.as_pointer()]
    hits = source["popup_hits" if popup else "hits"]
    rect = next(rect for rect, found, arg in hits if found == action and (value is None or arg == value))
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    event('LEFTMOUSE', 'PRESS', x, y)
    if before_release:
        before_release()
    event('LEFTMOUSE', 'RELEASE', x, y)


def show(kind):
    area, region = area_region()
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert chat_view.show_popover(bpy.context, kind)
    redraw()


def tick():
    global window, handler
    try:
        if time.monotonic() - state["started"] > 60:
            raise RuntimeError("Native picker QA timed out")
        if state["phase"] == "boot":
            window = bpy.context.window_manager.windows[0]
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "layout"
            return 0.6
        if ui._layout_timer is not None:
            return 0.05
        area, region = area_region()
        controller = ui.get_controller()
        source = chat_view._views.setdefault(area.as_pointer(), {})
        if state["phase"] == "layout":
            controller.models = copy.deepcopy(MODELS)
            controller.refresh_models = lambda: None
            controller.set_options(model="gpt-6.1-sol", effort="max", fast=False)
            controller.state["messages"] = [{"role": "user", "text": "Help me improve this scene."},
                                            {"role": "assistant", "text": "Choose a model and reasoning effort below. Fast mode is a separate, explicit switch."}]
            controller.version += 1
            controller.turn_options = dict(controller.state["options"])
            with bpy.context.temp_override(window=window, area=area, region=region):
                console.render(bpy.context, force=True)
            area.spaces.active.history[-1].body = "Draft must survive model selection."
            handler = SimpleNamespace(window_pointer=window.as_pointer(), focused_area=area.as_pointer(), session=controller.state["id"],
                                      selected_all=False, anchor=None, dragging=False, pending_click=None)
            def closed_on_press():
                assert not source.get("popover"), "A picker must not inherit a held mouse gesture"
            click("model", popup=False, before_release=closed_on_press)
            assert source["popover"]["kind"] == "controls"
            state["checks"]["ordinary_click_opens_compact_picker"] = True
            state["phase"] = "controls"
            return 0.3
        if state["phase"] == "controls":
            capture("reasoning-fast-picker.png")
            state["motion_before"] = state["captures"]["reasoning-fast-picker.png"]["pixel_hash"]
            state["phase"] = "animation"
            return 0.25
        if state["phase"] == "animation":
            capture("reasoning-animation-second-frame.png")
            assert state["motion_before"] != state["captures"]["reasoning-animation-second-frame.png"]["pixel_hash"]
            state["checks"]["blue_sparkles_animate_without_user_input"] = True
            state["reduce_motion_before"] = bpy.context.preferences.view.use_reduce_motion
            bpy.context.preferences.view.use_reduce_motion = True
            capture("reasoning-reduced-motion.png")
            state["phase"] = "reduced_motion"
            return 0.25
        if state["phase"] == "reduced_motion":
            capture("reasoning-reduced-motion-second-frame.png")
            assert state["captures"]["reasoning-reduced-motion.png"]["pixel_hash"] == state["captures"]["reasoning-reduced-motion-second-frame.png"]["pixel_hash"]
            bpy.context.preferences.view.use_reduce_motion = state["reduce_motion_before"]
            state["checks"]["native_reduce_motion_preference_is_respected"] = True
            controller.set_options(effort="xhigh")
            state["phase"] = "partial_fill"
            return 0.35
        if state["phase"] == "partial_fill":
            capture("reasoning-extra-high-partial-fill.png")
            slider = source["popover"]["slider_draw"]
            assert slider["unselected_start"] < source["popover"]["layout"]["slider"][0] + source["popover"]["layout"]["slider"][2] - 20
            state["checks"]["lower_effort_leaves_dark_track_to_the_right"] = True
            assert sum(action == "popup_fast" for _, action, _ in source["popup_hits"]) == 1, "Use only lightning, not a redundant switch"
            state["checks"]["lightning_is_the_only_fast_toggle"] = True
            state["checks"]["lightning_slider_model_and_reset_have_hits"] = {"popup_fast", "popup_slider", "popup_models", "popup_reset"} <= {action for _, action, _ in source["popup_hits"]}
            track = source["popover"]["layout"]["slider"]
            event('LEFTMOUSE', 'PRESS', track[0] + 10, track[1] + 14)
            event('MOUSEMOVE', 'NOTHING', track[0] + track[2] - 8, track[1] + 14)
            event('LEFTMOUSE', 'RELEASE', track[0] + track[2] - 8, track[1] + 14)
            assert controller.state["options"]["effort"] == "max" and not controller.state["options"]["fast"]
            click("popup_reset")
            assert controller.state["options"]["effort"] == "medium" and not controller.state["options"]["fast"]
            event('END')
            assert controller.state["options"]["effort"] == "max"
            state["checks"]["slider_keyboard_reset_do_not_enable_paid_fast"] = True
            click("popup_fast")
            assert source["popover"]["kind"] == "fast_confirm" and not controller.state["options"]["fast"]
            event('RET')
            assert not controller.state["options"]["fast"], "Enter must not accidentally approve billing"
            state["phase"] = "confirmation"
            return 0.3
        if state["phase"] == "confirmation":
            capture("fast-confirmation.png")
            click("popup_cancel_fast")
            assert source["popover"]["kind"] == "controls" and not controller.state["options"]["fast"]
            click("popup_fast")
            def no_billing_on_press():
                assert not controller.state["options"]["fast"]
            click("popup_enable_fast", before_release=no_billing_on_press)
            assert controller.state["options"]["fast"] and not controller.turn_options["fast"]
            state["checks"]["fast_requires_explicit_confirm_and_keeps_captured_turn"] = True
            state["phase"] = "fast_on"
            return 0.3
        if state["phase"] == "fast_on":
            capture("reasoning-fast-on.png")
            click("popup_models")
            assert source["popover"]["kind"] == "models"
            state["phase"] = "models"
            return 0.3
        if state["phase"] == "models":
            capture("model-selection.png")
            assert [r["id"] for r in source["popover"]["rows"] if r["selected"]] == ["gpt-6.1-sol"]
            controller.busy = True
            try:
                click("popup_select_model", "gpt-6-astra")
                assert controller.state["options"]["model"] == "gpt-6-astra"
                assert controller.turn_options["model"] == "gpt-6.1-sol"
            finally:
                controller.busy = False
            assert not source.get("popover")
            state["checks"]["selection_applies_next_turn_not_current_turn"] = True
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.angelo.model_picker('INVOKE_DEFAULT')
            assert 'FINISHED' in result and source["popover"]["kind"] == "models", "Chat must not call the stock Blender model menu"
            redraw()
            click("popup_select_model", "")
            assert [r["id"] for r in model_rows(controller.models, controller.state["options"]["model"]) if r["selected"]] == [""]
            state["checks"]["default_has_one_checkmark_and_native_op_uses_custom_popup"] = True
            controller.models.extend([{**MODELS[1], "id": f"qa-{i}", "name": f"QA test model {i}"} for i in range(60)])
            show("models")
            scroll_before = chat_view._scroll.get(controller.state["id"], 0)
            event('WHEELDOWNMOUSE')
            assert source["popover"]["scroll"] == 58 and chat_view._scroll.get(controller.state["id"], 0) == scroll_before
            redraw()
            event('END')
            assert source["popover"]["scroll"] == source["popover"]["layout"]["max_scroll"]
            event('RET')
            assert controller.state["options"]["model"] == "qa-59"
            controller.models = copy.deepcopy(MODELS)
            controller.set_options(model="gpt-6.1-sol", fast=False)
            state["checks"]["long_catalog_scroll_and_keyboard_navigation"] = True
            show("controls")
            event('Z', unicode="z")
            assert area.spaces.active.history[-1].body == "Draft must survive model selection."
            draft_before = area.spaces.active.history[-1].body
            message_count = len(controller.state["messages"])
            send = next(rect for rect, action, _ in source["hits"] if action == "send")
            event('LEFTMOUSE', 'PRESS', send[0] + 14, send[1] + 14)
            event('LEFTMOUSE', 'RELEASE', send[0] + 14, send[1] + 14)
            assert not source.get("popover") and len(controller.state["messages"]) == message_count
            assert area.spaces.active.history[-1].body == draft_before
            state["checks"]["typing_and_outside_click_never_reach_composer"] = True
            click("fast", popup=False)
            assert source["popover"]["kind"] == "fast_confirm" and not controller.state["options"]["fast"]
            click("popup_enable_fast")
            assert controller.state["options"]["fast"]
            event('ESC')
            click("fast", popup=False)
            assert not controller.state["options"]["fast"] and not source.get("popover")
            state["checks"]["composer_lightning_also_toggles_fast"] = True
            controller.models[0]["fast_tier"] = None
            show("controls")
            assert not any(action == "popup_fast" for _, action, _ in source["popup_hits"])
            assert not any(action == "fast" for _, action, _ in source["hits"])
            assert source["popover"]["layout"]["rect"][3] == 100
            state["checks"]["unsupported_fast_switch_and_lightning_are_hidden"] = True
            state["phase"] = "unsupported"
            return 0.3
        if state["phase"] == "unsupported":
            capture("fast-unavailable.png")
            event('ESC')
            assert not source.get("popover")
            click("approval", popup=False)
            assert source["popover"]["kind"] == "approval"
            state["phase"] = "approval"
            return 0.3
        if state["phase"] == "approval":
            capture("approval-picker.png")
            click("popup_approval", "READ_ONLY")
            assert controller.state["options"]["approval"] == "READ_ONLY" and not source.get("popover")
            state["checks"]["approval_is_also_a_normal_click_popup"] = True
            show("controls")
            source["popover"]["connection"] = "stale-connection"
            redraw()
            assert not source.get("popover")
            state["checks"]["connection_change_invalidates_old_popup"] = True
            # Connected catalogs are fixture data: exercise real native picking
            # and controller routing without either subscription being billed.
            first = ui.connections().add("OPENAI", "SUBSCRIPTION", "ChatGPT test", reuse_native=True)
            second = ui.connections().add("ANTHROPIC", "SUBSCRIPTION", "Claude test", reuse_native=True)
            ui._authentication.status.update(codex_subscription="Connected to ChatGPT", claude_subscription="Connected to Claude")
            ui.refresh_connected_models = lambda *args, **kwargs: None
            ui.catalogs().entries[first["id"]] = copy.deepcopy(MODELS)
            native_claude = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                "supportedEffortLevels": ["low", "medium", "high"]}], "claude")
            ui.catalogs().entries[second["id"]] = native_claude
            controller.choose_connection("codex_subscription", ui.connection_settings("codex_subscription", record=first), "", first["id"], MODELS, "gpt-6.1-sol")
            controller.state["native_id"] = "fixture-gpt-native"
            state["shared_chat"] = {"id": controller.state["id"], "message_count": len(controller.state["messages"]),
                                    "draft": area.spaces.active.history[-1].body, "first": first["id"], "second": second["id"]}
            show("models")
            event('END')
            redraw()
            state["phase"] = "unified_models"
            return 0.3
        if state["phase"] == "unified_models":
            capture("shared-model-picker.png")
            rows = source["popover"]["rows"]
            assert any("ChatGPT test" in r["subtitle"] for r in rows)
            assert any("Claude test" in r["subtitle"] for r in rows)
            second = state["shared_chat"]["second"]
            controller.turn_options = dict(controller.state["options"])
            controller.busy = True
            try:
                click("popup_select_model", (second, "sonnet"))
                assert controller.state["provider"] == "codex_subscription"
                assert controller.selection_provider == "claude_subscription"
                assert controller.turn_options["model"] == "gpt-6.1-sol"
                assert not controller.state["options"]["fast"]
            finally:
                controller.busy = False
            controller.apply_next_connection()
            assert controller.state["provider"] == "claude_subscription"
            assert controller.state["id"] == state["shared_chat"]["id"]
            assert len(controller.state["messages"]) == state["shared_chat"]["message_count"]
            assert area.spaces.active.history[-1].body == state["shared_chat"]["draft"]
            state["checks"]["both_provider_catalogs_in_one_native_picker"] = True
            state["checks"]["cross_provider_choice_queues_without_changing_current_request"] = True
            state["checks"]["provider_switch_preserves_chat_history_and_draft"] = True
            controller.state["native_id"] = "fixture-claude-native"
            show("models")
            event('HOME')
            redraw()
            click("popup_select_model", (state["shared_chat"]["first"], "gpt-6.1-sol"))
            assert controller.state["native_id"] == "fixture-gpt-native"
            assert controller.state["id"] == state["shared_chat"]["id"]
            state["checks"]["return_to_provider_resumes_its_own_context"] = True
            state["checks"]["no_draw_errors"] = chat_view.last_error is None
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            record()
            print("ANGELO_CHAT_POPOVERS_TEST_OK", json.dumps(state["checks"]), flush=True)
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
