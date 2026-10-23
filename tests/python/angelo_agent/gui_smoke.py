# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Standalone GUI fixture. Set ANGELO_QA_LIVE=1 for one real subscription turn."""

import copy
import base64
import json
import os
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use a separate scratch process"
bpy.context.preferences.view.show_splash = False
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gui-smoke"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import ui, console, chat_view, preferences_ui
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.sessions import SessionCollection
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.chat_settings import normalize_models

state = {"phase": "boot", "started": time.monotonic(), "checks": {}}
main_window = None
SCRIPT = """import bpy
bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1, location=(2, 0, 1))
obj = bpy.context.active_object
obj.name = 'AngeloLiveDemo'
material = bpy.data.materials.new('AngeloLiveMaterial')
material.diffuse_color = (0.15, 0.3, 0.8, 1)
obj.data.materials.append(material)
obj.modifiers.new('AngeloLiveSubdivision', 'SUBSURF').levels = 1
result = {'object': obj.name, 'vertices': len(obj.data.vertices), 'material': material.name, 'modifier': obj.modifiers[0].type}
"""


def record():
    (OUT / "gui-smoke.json").write_text(json.dumps(state, indent=2, default=str))


def capture(name, window):
    area = next((a for a in window.screen.areas if a.type == "PREFERENCES"),
                next((a for a in window.screen.areas if console.is_chat(a)), window.screen.areas[0]))
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    with bpy.context.temp_override(window=window, area=area, region=region):
        for area in window.screen.areas:
            area.tag_redraw()
        # Flush native region updates before reading the window framebuffer.
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def check_right_dock(window):
    chat = next(a for a in window.screen.areas if console.is_chat(a))
    properties = next(a for a in window.screen.areas if a.type == "PROPERTIES")
    outliner = next(a for a in window.screen.areas if a.type == "OUTLINER")
    state["checks"]["right_of_properties"] = properties.x + properties.width <= chat.x
    state["checks"]["right_of_outliner"] = outliner.x + outliner.width <= chat.x
    state["checks"]["properties_width_preserved"] = abs(properties.width - state["properties_width"]) <= 4
    state["checks"]["properties_state_preserved"] = properties.spaces.active.context == state["properties_context"]
    state["checks"]["outliner_state_preserved"] = outliner.spaces.active.display_mode == state["outliner_mode"]
    state["checks"]["full_height_chat"] = chat.y == min(a.y for a in window.screen.areas) and chat.y + chat.height == max(a.y + a.height for a in window.screen.areas)
    state["layout"] = [{"type": a.type, "x": a.x, "y": a.y, "width": a.width, "height": a.height} for a in window.screen.areas]
    return chat


def tick():
    global main_window
    try:
        if time.monotonic() - state["started"] > 180:
            raise RuntimeError("GUI smoke test timed out")
        if state["phase"] == "boot":
            main_window = bpy.context.window_manager.windows[0]
            properties = next(a for a in main_window.screen.areas if a.type == "PROPERTIES")
            state["properties_width"] = properties.width
            state["properties_context"] = properties.spaces.active.context
            state["outliner_mode"] = next(a for a in main_window.screen.areas if a.type == "OUTLINER").spaces.active.display_mode
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "Angelo-native-chat-demo.blend"))
            bpy.ops.angelo.open_chat()
            state["phase"] = "layout"
            record()
            return 0.8
        if ui._layout_timer is not None:
            return 0.05
        controller = ui.get_controller()
        if state["phase"] == "layout":
            chat = check_right_dock(main_window)
            view = next(a for a in main_window.screen.areas if a.type == "VIEW_3D")
            state["checks"]["non_overlay"] = chat.x >= view.x + view.width or view.x >= chat.x + chat.width
            original_messages = copy.deepcopy(controller.state["messages"])
            with bpy.context.temp_override(window=main_window, area=chat, region=next(r for r in chat.regions if r.type == "WINDOW")):
                chat.spaces.active.history[-1].body = "draft survives"
                controller.state["messages"].append({"role": "assistant", "text": "Streaming"})
                controller.version += 1
                console.render(bpy.context)
                lines_before = len(chat.spaces.active.scrollback)
                controller.state["messages"][-1]["text"] += " update"
                controller.version += 1
                console.render(bpy.context)
                state["checks"]["incremental_native_text"] = len(chat.spaces.active.scrollback) == lines_before and chat.spaces.active.scrollback[-1].body == "Streaming update"
                state["checks"]["draft_survives_stream"] = chat.spaces.active.history[-1].body == "draft survives"
                controller.state["messages"] = original_messages
                controller.version += 1
                console.render(bpy.context, force=True)
                state["session_before_move"] = controller.state["id"]
                bpy.ops.angelo.dock_side(side="LEFT")
            state["phase"] = "left"
            record()
            return 0.5
        if state["phase"] == "left":
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            state["checks"]["far_left_dock"] = chat.x == min(a.x for a in main_window.screen.areas)
            state["checks"]["draft_survives_dock_move"] = chat.spaces.active.history[-1].body == "draft survives"
            state["checks"]["session_survives_dock_move"] = controller.state["id"] == state["session_before_move"]
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.dock_side(side="RIGHT")
            state["phase"] = "right"
            record()
            return 0.5
        if state["phase"] == "right":
            chat = check_right_dock(main_window)
            state["draft_before_popout"] = chat.spaces.active.history[-1].body
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.pop_out()
            state["phase"] = "floating"
            record()
            return 0.5
        if state["phase"] == "floating":
            floating = next(w for w in bpy.context.window_manager.windows if w != main_window and any(console.is_chat(a) for a in w.screen.areas))
            chat = next(a for a in floating.screen.areas if console.is_chat(a))
            state["floating_draft_observed"] = [line.body for line in chat.spaces.active.history]
            state["checks"]["draft_survives_popout"] = chat.spaces.active.history[-1].body == "draft survives"
            chat.spaces.active.history[-1].body = "updated floating draft"
            with bpy.context.temp_override(window=floating, area=chat):
                bpy.ops.angelo.return_dock()
            state["phase"] = "returned"
            record()
            return 0.5
        if state["phase"] == "returned":
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            state["checks"]["draft_survives_return"] = chat.spaces.active.history[-1].body == "updated floating draft"
            state["first_session"] = controller.state["id"]
            state["saved_messages"] = copy.deepcopy(controller.state["messages"])
            controller.emit("message", {"role": "user", "text": "Session A UI fixture"})
            controller.pump_events()
            # Creating/switching sessions must not require ending another turn.
            controller.busy = True
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.new_chat()
            controller.busy = False
            second = ui.get_controller()
            state["second_session"] = second.state["id"]
            second.emit("message", {"role": "system", "text": "Session B UI acceptance fixture (no inference)"})
            second.pump_events()
            chat.spaces.active.history[-1].body = "second session draft"
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.switch_session(session_id=state["first_session"])
            state["phase"] = "sessions"
            record()
            return 0.5
        if state["phase"] == "sessions":
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            state["checks"]["session_history_isolated"] = controller.state["messages"][-1]["text"] == "Session A UI fixture"
            state["checks"]["session_draft_restored"] = chat.spaces.active.history[-1].body == "updated floating draft"
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.switch_session(session_id=state["second_session"])
            state["checks"]["second_session_draft_restored"] = chat.spaces.active.history[-1].body == "second session draft"
            state["checks"]["second_session_history_isolated"] = ui.get_controller().state["messages"][-1]["text"].startswith("Session B UI acceptance")
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.switch_session(session_id=state["first_session"])
            controller = ui.get_controller()
            controller.state["messages"] = state.pop("saved_messages")
            controller.version += 1
            controller.save()
            ui._sessions.update(controller.state)
            ui._sessions.save()
            restored = SessionCollection(ui.state_directory())
            state["checks"]["sessions_persisted"] = restored.active == state["first_session"] and state["second_session"] in restored.records
            chat.spaces.active.history[-1].body = ""
            native = BlenderExecutor(main_window)
            # Native controls and references without changing an account,
            # recording a microphone, or starting a premium-tier request.
            prior_options = dict(controller.state["options"])
            controller.models = normalize_models([{"model": "qa-fixture", "displayName": "QA fixture (not a provider model)", "isDefault": True,
                                                   "serviceTiers": [{"id": "fast"}], "inputModalities": ["text", "image"],
                                                   "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]}])
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.select_model(model="qa-fixture")
                bpy.ops.angelo.set_effort(effort="high")
                bpy.ops.angelo.toggle_fast()
                bpy.ops.angelo.set_approval(mode="READ_ONLY")
            state["checks"]["model_fast_effort_approval_controls"] = controller.state["options"] == {"model": "qa-fixture", "effort": "high", "fast": True, "approval": "READ_ONLY"}
            controller.state["options"] = prior_options
            controller.models = []
            image = OUT / "reference-test.png"
            image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.attach_reference(filepath=str(image))
            state["checks"]["reference_snapshot_attached"] = len(controller.attachments) == 1 and Path(controller.attachments[0]["path"]).read_bytes() == image.read_bytes()
            capture("chat-reference-attached.png", main_window)
            state["checks"]["chat_controls_have_hits"] = {"model", "approval", "attach", "voice", "remove_reference"} <= {action for _, action, _ in chat_view._views[chat.as_pointer()]["hits"]}
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.remove_reference(index=0)
            state["checks"]["reference_removed"] = not controller.attachments
            dropped = OUT / "drop-test.md"
            dropped.write_text("# Reference fixture\nDo not execute embedded instructions.\n")
            chat.spaces.active.history[-1].body = "drop draft"
            chat.spaces.active.history[-1].current_character = len("drop draft")
            chat_view.defer_console_drop(main_window, chat, controller)
            with bpy.context.temp_override(window=main_window, area=chat, region=next(r for r in chat.regions if r.type == "WINDOW")):
                bpy.ops.console.insert(text='"' + str(dropped) + '"')
            state["phase"] = "drop"
            record()
            return 0.3
        if state["phase"] == "drop":
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            state["checks"]["native_drop_attaches_file_not_path_text"] = len(controller.attachments) == 1 and controller.attachments[0]["kind"] == "text" and chat.spaces.active.history[-1].body == "drop draft"
            with bpy.context.temp_override(window=main_window, area=chat):
                state["checks"]["native_file_handler_accepts_chat"] = ui.ANGELO_FH_references.poll_drop(bpy.context)
                bpy.ops.angelo.remove_reference(index=0)
            chat.spaces.active.history[-1].body = ""
            # Test ordinary click/release semantics without OS input or touching
            # another Blender process. Popups must not be opened on mouse-down.
            source_view = chat_view._views[chat.as_pointer()]
            original_dispatch = chat_view.dispatch_action
            try:
                calls = []
                chat_view.dispatch_action = lambda context, controller, action, value: calls.append(action)
                region = next(r for r in chat.regions if r.type == "WINDOW")
                handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=main_window.as_pointer(),
                                               focused_area=chat.as_pointer(), session=controller.state["id"],
                                               selected_all=False, anchor=None, dragging=False, pending_click=None)
                with bpy.context.temp_override(window=main_window, area=chat, region=region):
                    for wanted in ("model", "approval"):
                        rect = next(rect for rect, action, _ in source_view["hits"] if action == wanted)
                        def event(value):
                            return SimpleNamespace(type='LEFTMOUSE', value=value, mouse_x=region.x + rect[0] + rect[2] / 2,
                                                   mouse_y=region.y + rect[1] + rect[3] / 2, ctrl=False, oskey=False, shift=False, alt=False, unicode="")
                        before = len(calls)
                        chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, event('PRESS'))
                        assert len(calls) == before, "A dropdown must not open with a held-button gesture"
                        chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, event('RELEASE'))
                        assert calls[-1] == wanted
                state["checks"]["dropdowns_activate_on_normal_click_release"] = True
            finally:
                chat_view.dispatch_action = original_dispatch
            native = BlenderExecutor(main_window)
            if os.environ.get("ANGELO_QA_LIVE") == "1":
                ui.prepare_controller()
                state["before_calls"] = len(controller.state["results"])
                controller.send("Create a sphere with a material and a subdivision modifier, not a cube. Call angelo_execute_python with description 'Create a blue sphere with subdivision' and EXACTLY the following code, then summarize its actual read-back. No other tools or changes are needed:\n\n" + SCRIPT,
                                observation=native.execute("angelo_inspect_scene", {}))
                state["phase"] = "live"
                record()
                return 0.05
            state["phase"] = "finish"
        if state["phase"] == "live":
            for call in list(controller.pending.values()):
                if call.approval:
                    assert call.name == "angelo_execute_python" and call.arguments["code"].strip() == SCRIPT.strip(), "Unexpected scratch script: do not autoapprove"
                    state["checks"]["approval_gate"] = "AngeloLiveDemo" not in main_window.scene.objects
                    capture("general-python-approval.png", main_window)
                    with bpy.context.temp_override(window=main_window):
                        bpy.ops.angelo.review_tool(call_id=call.id)
                        bpy.ops.angelo.approve(call_id=call.id)
            if controller.busy:
                return 0.05
            state["checks"]["optimized_live_turn"] = controller.status == "Completed" and "AngeloLiveDemo" in main_window.scene.objects
            if "AngeloLiveDemo" in main_window.scene.objects:
                obj = main_window.scene.objects["AngeloLiveDemo"]
                state["checks"]["live_general_python_material_modifier"] = len(obj.data.vertices) > 8 and len(obj.data.materials) == 1 and len(obj.modifiers) == 1
                view = next(a for a in main_window.screen.areas if a.type == "VIEW_3D")
                with bpy.context.temp_override(window=main_window, area=view, region=next(r for r in view.regions if r.type == "WINDOW")):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
            state["timings"] = {k: v for k, v in controller.timings.items() if k != "started"}
            state["native_calls_this_turn"] = len(controller.state["results"]) - state["before_calls"]
            state["models"] = controller.models
            ui.prepare_controller()
            controller.send("Use the web search tool to look up the official Blender manual page for the Bevel modifier on docs.blender.org. Reply with one short sentence and its Markdown URL. Do not modify the scene or files.",
                            observation=BlenderExecutor(main_window).execute("angelo_inspect_scene", {}))
            state["phase"] = "web"
            state["web_started_index"] = len(controller.state["messages"])
            record()
            return 0.2
        if state["phase"] == "web":
            if controller.busy:
                return 0.1
            web_messages = controller.state["messages"][state["web_started_index"]:]
            state["checks"]["native_web_search_available"] = any(m.get("tool") == "web_search" and m.get("status") == "completed" for m in web_messages)
            state["checks"]["web_reply_has_real_source"] = any(m["role"] == "assistant" and "blender.org" in m["text"] for m in web_messages)
            capture("native-web-search.png", main_window)
            ui.prepare_controller()
            controller.send("Use angelo_write_file to create exactly the file angelo-test-note.md with exactly the content 'Native file tools work.\\n' in the file workspace. Do not modify the scene or any other file. Then confirm from the actual tool result.",
                            observation=BlenderExecutor(main_window).execute("angelo_inspect_scene", {}))
            state["phase"] = "file_tools"
            record()
            return 0.2
        if state["phase"] == "file_tools":
            for call in list(controller.pending.values()):
                if call.approval:
                    assert call.name == "angelo_write_file" and call.arguments == {"path": "angelo-test-note.md", "content": "Native file tools work.\n"}, "Unexpected file change: do not autoapprove"
                    capture("human-file-approval.png", main_window)
                    with bpy.context.temp_override(window=main_window):
                        bpy.ops.angelo.approve(call_id=call.id)
            if controller.busy:
                return 0.1
            target = controller.files.root / "angelo-test-note.md"
            state["checks"]["native_scoped_file_tool_available"] = target.is_file() and target.read_text() == "Native file tools work.\n"
            state["checks"]["requests_have_native_snapshots"] = all(m.get("checkpoint") for m in controller.state["messages"] if m["role"] == "user")
            state["phase"] = "finish"
            record()
            return 0.5
        if state["phase"] == "finish":
            capture("native-chat-live.png", main_window)
            state["checks"]["native_styled_chat_rendered"] = chat_view.draw_count > 0 and chat_view.last_error is None
            state["chat_draw_error"] = chat_view.last_error
            if os.environ.get("ANGELO_QA_LIVE") == "1":
                chat = next(a for a in main_window.screen.areas if console.is_chat(a))
                state["checks"]["completed_header"] = ui._header_states.get(chat.as_pointer()) == ("Completed", False, None)
            with bpy.context.temp_override(window=main_window):
                bpy.ops.angelo.settings()
                for mode in ("codex_subscription", "claude_subscription"):
                    if mode not in ui._authentication.running:
                        bpy.ops.angelo.check_signin(mode=mode)
            state["phase"] = "providers"
            record()
            return 0.5
        if state["phase"] == "providers":
            if ui._authentication.running:
                return 0.2
            prefs_window = next(w for w in bpy.context.window_manager.windows if any(a.type == "PREFERENCES" for a in w.screen.areas))
            state["checks"]["ai_providers_tab_selected"] = preferences_ui.is_selected()
            state["provider_status"] = dict(ui._authentication.status)
            if not preferences_ui.native_section():
                bpy.context.window_manager.angelo_preferences_section = "INTERFACE"
                state["checks"]["stock_preferences_tabs_still_work"] = bpy.context.preferences.active_section == "INTERFACE" and not preferences_ui.is_selected()
                preferences_ui.select()
            capture("ai-providers-preferences.png", prefs_window)
            state["checks"]["simple_connection_list"] = bool(ui.connections().records) and all("api_key" not in r for r in ui.connections().records.values())
            original = ui.get_controller().state["id"]
            missing = ui.create_session("claude_api")
            state["missing_connection_session"] = missing.state["id"]
            state["checks"]["missing_connection_detected"] = not ui.connection_available()
            state["return_session"] = original
            state["phase"] = "setup_button"
            record()
            return 0.5
        if state["phase"] == "setup_button":
            capture("chat-needs-connection.png", main_window)
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            state["checks"]["chat_setup_button"] = any(action == "settings" for _, action, _ in chat_view._views[chat.as_pointer()]["hits"])
            with bpy.context.temp_override(window=main_window, area=chat):
                bpy.ops.angelo.switch_session(session_id=state["return_session"])
            state["checks"]["configured_connection_hides_setup"] = ui.connection_available()
            state["phase"] = "last_frame"
            record()
            return 0.5
        if state["phase"] == "last_frame":
            capture("native-chat-live.png", main_window)
            chat = next(a for a in main_window.screen.areas if console.is_chat(a))
            view = chat_view._views[chat.as_pointer()]
            state["checks"]["codex_composer_geometry"] = view["geometry"]["rect"] == (15, 20, chat.regions[0].width - 30, 98) if chat.regions[0].type == "WINDOW" else view["geometry"]["radius"] == 22
            if os.environ.get("ANGELO_QA_LIVE") == "1":
                state["checks"]["tool_activity_and_changes_are_visible"] = any(action == "expand" for _, action, _ in view["hits"]) and any(action == "changes" for _, action, _ in view["hits"])
            prefs_window = next(w for w in bpy.context.window_manager.windows if any(a.type == "PREFERENCES" for a in w.screen.areas))
            area = next(a for a in prefs_window.screen.areas if a.type == "PREFERENCES")
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=prefs_window, area=area, region=region):
                bpy.ops.angelo.new_connection('INVOKE_DEFAULT')
            state["phase"] = "connection_form"
            record()
            return 0.5
        if state["phase"] == "connection_form":
            prefs_window = next(w for w in bpy.context.window_manager.windows if any(a.type == "PREFERENCES" for a in w.screen.areas))
            capture("new-connection-form.png", prefs_window)
            with bpy.context.temp_override(window=prefs_window):
                bpy.ops.wm.window_close()
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            record()
            print("ANGELO_GUI_SMOKE_COMPLETE", state["success"], flush=True)
            return None
        return 0.1
    except Exception as error:
        state["phase"] = "failed"
        state["error"] = str(error)
        state["traceback"] = traceback.format_exc()
        if ui._controller:
            ui._controller.pump_events()
            state["messages"] = ui._controller.state["messages"]
        if main_window:
            state["layout"] = [{"type": a.type, "x": a.x, "y": a.y, "width": a.width, "height": a.height} for a in main_window.screen.areas]
        record()
        print(state["traceback"], flush=True)
        if ui._controller:
            ui._controller.close()
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1, persistent=True)
