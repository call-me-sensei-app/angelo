# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled editor GUI acceptance using native operators/synthetic UI events.

No authentication, inference, paid tiers, microphone or physical OS file drops.
"""

import json
import os
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv
assert "is_coding_agent" in bpy.types.SpaceConsole.bl_rna.properties, "Run the compiled fork"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-compiled-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
run_id = uuid.uuid4().hex
os.environ["ANGELO_STATE_DIR"] = str(OUT / ("state-" + run_id))
os.environ["ANGELO_CONNECTIONS_DIR"] = str(OUT / ("accounts-" + run_id))
runtime_temp = OUT / ("runtime-" + run_id)
runtime_temp.mkdir()
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.filepaths.temporary_directory = str(runtime_temp)
installed_addons = Path(bpy.utils.system_resource("SCRIPTS")) / "addons_core"
assert (installed_addons / "angelo_agent").is_dir(), "Install the compiled app's bundled host before GUI acceptance"
sys.path.insert(0, str(installed_addons))
from angelo_agent import ui, console, editor_types, chat_view, preferences_ui
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

# Importing the host for fixture setup does not enable its addon. Exercise the
# native console language's deferred bootstrap, with all account probes disabled.
account_requests = []
ui._authentication.start = lambda *args, **kwargs: account_requests.append((args, kwargs))
ui.refresh_connected_models = lambda *args, **kwargs: None
bpy.context.preferences.view.show_splash = False
state = {"phase": "boot", "checks": {}, "success": False, "binary": bpy.app.binary_path,
         "host_modules": str(Path(ui.__file__).parent),
         "fixture": "Native operators and synthetic keyboard/menu events; no live providers or physical file drops"}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
started = time.monotonic()
main_pointer = bpy.context.window_manager.windows[0].as_pointer()
area_pointer = None


def main():
    return next(w for w in bpy.context.window_manager.windows if w.as_pointer() == main_pointer)


def target(window=None):
    return next(a for a in (window or main()).screen.areas if a.as_pointer() == area_pointer)


def select(identity):
    window, area = main(), target()
    with bpy.context.temp_override(window=window, area=area):
        area.ui_type = identity
    assert area.ui_type == identity


def execute_python(text):
    window, area = main(), target()
    assert area.spaces.active.language == "python"
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        area.spaces.active.history[-1].body = text
        bpy.ops.console.execute()


def capture(name, window=None):
    with bpy.context.temp_override(window=window or main()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def save():
    (OUT / "compiled-gui.json").write_text(json.dumps(state, indent=2))


def tick():
    global area_pointer
    try:
        assert time.monotonic() - started < 90, "GUI acceptance timed out"
        window = main()
        phase = state["phase"]
        if phase == "boot":
            # Keep the actual scene viewport available to the dock/executor.
            area_pointer = next(a for a in window.screen.areas if a.type == "PROPERTIES").as_pointer()
            state["checks"]["host_not_pre_enabled"] = "angelo_agent" not in bpy.context.preferences.addons
            select("CONSOLE")
            state["phase"] = "python"
        elif phase == "python":
            execute_python("angelo_compiled_gui_probe = 17; print('COMPILED_GUI_PYTHON', angelo_compiled_gui_probe)")
            target().spaces.active.history[-1].body = "print('saved native Python draft')"
            select("CODING_AGENT")
            state["phase"] = "bootstrap"
        elif phase == "bootstrap":
            # Enabling the host schedules the session's first Studio/Flows tab build, which switches this window
            # through the tabs it makes for a few frames: the area is back once the gate opens, never before.
            if ("angelo_agent" not in bpy.context.preferences.addons or not qa_boot.started()
                    or not target().spaces.active.history):
                return .1
            state["checks"]["native_choice_lazily_enables_host"] = console.is_chat(target())
            state["checks"]["native_picker_without_header_monkeypatches"] = editor_types.native_picker() and not editor_types._headers
            state["checks"]["native_preferences_without_navigation_proxy"] = preferences_ui._native and not hasattr(bpy.types.WindowManager, "angelo_preferences_section")
            state["checks"]["python_draft_not_promoted_to_chat"] = target().spaces.active.history[-1].body != "print('saved native Python draft')"
            state["checks"]["native_selection_starts_input_handler"] = window.as_pointer() in chat_view._modals
            target().spaces.active.history[-1].body = "Unsent native chat draft — never Python"
            capture("native-coding-agent.png")
            header = next(r for r in target().regions if r.type == "HEADER")
            x, y = header.x + 16, header.y + header.height // 2
            window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
            window.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
            window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
            state["phase"] = "picker"
        elif phase == "picker":
            capture("native-editor-picker.png")
            window.event_simulate(type="ESC", value="PRESS")
            window.event_simulate(type="ESC", value="RELEASE")
            state["phase"] = "switch_back"
        elif phase == "switch_back":
            select("CONSOLE")
            state["phase"] = "restored_python"
        elif phase == "restored_python":
            state["checks"]["native_python_input_restored"] = target().spaces.active.history[-1].body == "print('saved native Python draft')"
            execute_python("print('COMPILED_GUI_NAMESPACE', angelo_compiled_gui_probe)")
            state["checks"]["native_python_namespace_retained"] = any("COMPILED_GUI_NAMESPACE 17" in l.body for l in target().spaces.active.scrollback)
            target().spaces.active.history[-1].body = "print('saved native Python draft')"
            with bpy.context.temp_override(window=window, area=target()):
                bpy.ops.angelo.editor_type(ui_type="CODING_AGENT")
            state["checks"]["host_selection_uses_cpp_buffers"] = target().spaces.active.history[-1].body == "Unsent native chat draft — never Python" and not editor_types._buffers
            region = next(r for r in target().regions if r.type == "WINDOW")
            x, y = region.x + region.width // 2, region.y + region.height // 2
            window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
            window.event_simulate(type="F4", value="PRESS", shift=True, x=x, y=y)
            window.event_simulate(type="F4", value="RELEASE", shift=True, x=x, y=y)
            state["phase"] = "shortcut"
        elif phase == "shortcut":
            state["checks"]["synthetic_shift_f4_opens_native_python"] = target().ui_type == "CONSOLE" and target().spaces.active.language == "python"
            state["checks"]["shortcut_does_not_execute_chat_draft"] = target().spaces.active.history[-1].body == "print('saved native Python draft')"
            select("CODING_AGENT")
            preferences_ui.select()
            with bpy.context.temp_override(window=window):
                bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
            state["phase"] = "preferences"
        elif phase == "preferences":
            pref_window = next(w for w in bpy.context.window_manager.windows if any(a.type == "PREFERENCES" for a in w.screen.areas))
            state["checks"]["actual_native_provider_preferences_window"] = bpy.context.preferences.active_section == "AI_PROVIDERS" and hasattr(bpy.types, "USERPREF_PT_ai_providers")
            capture("native-ai-providers.png", pref_window)
            with bpy.context.temp_override(window=pref_window):
                bpy.ops.wm.window_close()
            state["phase"] = "drops"
        elif phase == "drops":
            controller = ui.get_controller()
            folder = OUT / "drop-fixtures"
            folder.mkdir(exist_ok=True)
            fixtures = {"readme.txt": "Reference text", "reference.py": "raise RuntimeError('NEVER_EXECUTE_REFERENCE')", "reference.blend": "An opaque reference, not a document to open"}
            for name, text in fixtures.items():
                (folder / name).write_text(text)
            draft, document = target().spaces.active.history[-1].body, bpy.data.filepath
            region = next(r for r in target().regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=target(), region=region):
                bpy.ops.wm.drop_import_file(directory=str(folder) + os.sep, files=[{"name": name} for name in fixtures])
            state["checks"]["compiled_multifile_handler_attaches_all_references"] = {r["name"] for r in controller.attachments} == set(fixtures)
            state["checks"]["reference_drop_neither_executes_nor_opens_project"] = target().spaces.active.history[-1].body == draft and bpy.data.filepath == document
            controller.busy = True
            try:
                with bpy.context.temp_override(window=window, area=target(), region=region):
                    bpy.ops.wm.drop_import_file(directory=str(folder) + os.sep, files=[{"name": "reference.blend"}])
            finally:
                controller.busy = False
            state["checks"]["busy_drop_rejected_without_document_change"] = len(controller.attachments) == 3 and bpy.data.filepath == document and target().spaces.active.history[-1].body == draft
            state["manual_popout"] = True
            with bpy.context.temp_override(window=window, area=target(), region=region):
                assert 'FINISHED' in bpy.ops.angelo.pop_out()
            state["phase"] = "popout"
        elif phase == "dock":
            if not qa_boot.started():
                return .1
            assert ui._dock_area_pointer is not None, ui.get_controller().state["messages"]
            area_pointer = next(a.as_pointer() for a in window.screen.areas if console.is_chat(a))
            state["checks"]["native_chat_docks_without_replacing_viewport"] = any(a.type == "VIEW_3D" for a in window.screen.areas) and target().ui_type == "CODING_AGENT" and ui._dock_area_pointer == area_pointer
            state["checks"]["native_docking_restores_properties_editor"] = any(a.type == "PROPERTIES" for a in window.screen.areas)
            region = next(r for r in target().regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=target(), region=region):
                result = bpy.ops.angelo.pop_out()
            assert "FINISHED" in result
            state["manual_popout"] = False
            state["phase"] = "popout"
        elif phase == "popout":
            popped = next(w for w in bpy.context.window_manager.windows if w.as_pointer() != main_pointer)
            popped_area = next(a for a in popped.screen.areas if console.is_chat(a))
            state["checks"]["native_popout_keeps_chat_and_draft"] = popped_area.ui_type == "CODING_AGENT" and popped_area.spaces.active.history[-1].body == "Unsent native chat draft — never Python"
            state["checks"]["new_window_has_native_chat_input"] = popped.as_pointer() in chat_view._modals
            capture("native-popout.png", popped)
            region = next(r for r in popped_area.regions if r.type == "WINDOW")
            state["return_dock_context"] = {"main": main_pointer, "host_main": ui._main_window_pointer, "dock_area": ui._dock_area_pointer,
                                           "popped": popped.as_pointer(), "main_areas": [(a.as_pointer(), a.ui_type) for a in window.screen.areas]}
            with bpy.context.temp_override(window=popped, area=popped_area, region=region):
                receipt = bpy.ops.angelo.return_dock()
            state["return_dock_receipt"] = sorted(receipt)
            assert "FINISHED" in receipt, state["return_dock_context"]
            state["phase"] = "returned"
        elif phase == "returned":
            if not qa_boot.started():
                return .1
            chats = [a for a in window.screen.areas if console.is_chat(a)]
            assert chats, {"return": state.get("return_dock_context"), "areas": [(a.as_pointer(), a.ui_type, getattr(a.spaces.active, "language", "")) for a in window.screen.areas]}
            area_pointer = chats[0].as_pointer()
            if state["manual_popout"]:
                state["checks"]["native_selected_area_popout_returns_to_original_slot"] = len(bpy.context.window_manager.windows) == 1 and target().spaces.active.history[-1].body == "Unsent native chat draft — never Python"
                with bpy.context.temp_override(window=window, area=target()):
                    bpy.ops.angelo.open_chat()
                state["phase"] = "dock"
                save()
                return .1
            state["checks"]["native_return_to_dock_preserves_draft"] = len(bpy.context.window_manager.windows) == 1 and target().spaces.active.history[-1].body == "Unsent native chat draft — never Python"
            state["checks"]["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in window.scene.objects]
            state["checks"]["no_live_provider_started"] = not any(c.provider for c in ui._controllers.values())
            state["checks"]["no_native_buffer_compatibility_cache"] = not editor_types._buffers
            capture("native-docked-chat.png")
            addon_utils.disable("angelo_agent", default_set=False)
            state["checks"]["disable_returns_to_native_python"] = target().ui_type == "CONSOLE" and target().spaces.active.language == "python"
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            save()
            print("ANGELO_COMPILED_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        save()
        return .35
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


save()  # A prematurely closed window must not leave a stale success receipt.
bpy.app.timers.register(tick, first_interval=1)
