# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-billable GUI regressions: per-chat drafts, editor sub-types and the pop-out fallback."""

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
assert "coding_agent_input" in bpy.types.SpaceConsole.bl_rna.properties, "Rebuild the native draft API"
os.environ["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-editor-regressions"))
OUT.mkdir(parents=True, exist_ok=True)
private = OUT / uuid.uuid4().hex
private.mkdir()
os.environ["ANGELO_STATE_DIR"] = str(private / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(private / "accounts")
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.filepaths.temporary_directory = str(private)
bpy.context.preferences.view.show_splash = False
sys.path.insert(0, str(Path(bpy.utils.system_resource("SCRIPTS")) / "addons_core"))
from angelo_agent import ui, console, editor_types, docking
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *a, **kw: None
ui.refresh_connected_models = lambda *a, **kw: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
main = bpy.context.window_manager.windows[0]
ui._main_window_pointer = main.as_pointer()
area_pointer = next(a.as_pointer() for a in main.screen.areas if a.type == "PROPERTIES")
layout_workspace = main.workspace
state = {"phase": "boot", "success": False, "checks": {}, "binary": bpy.app.binary_path,
         "host_modules": str(Path(ui.__file__).parent), "fixture": "Native GUI only; no accounts, inference or scene edits"}
started = time.monotonic()
scene_before = [(o.name, tuple(o.location)) for o in main.scene.objects]
sessions = {}
subtypes = [("DRIVERS", None), ("ASSETS", None), ("UV", None), ("GeometryNodeTree", None),
            ("TIMELINE", None), ("IMAGE_EDITOR", "PAINT"), ("DOPESHEET", "ACTION")]
subtype_index = 0
original_relocate = docking.relocate_chat


def save():
    (OUT / "editor-regressions.json").write_text(json.dumps(state, indent=2))


def area():
    return next(a for a in main.screen.areas if a.as_pointer() == area_pointer)


def select(identity):
    with bpy.context.temp_override(window=main, area=area()):
        area().ui_type = identity


def popout():
    current = area()
    region = next(r for r in current.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=main, area=current, region=region):
        assert 'FINISHED' in bpy.ops.angelo.pop_out()


def popped_chat():
    popped = next(w for w in bpy.context.window_manager.windows if w.as_pointer() != main.as_pointer())
    return popped, next(a for a in popped.screen.areas if console.is_chat(a))


def return_popout():
    popped, chat = popped_chat()
    region = next(r for r in chat.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=popped, area=chat, region=region):
        return bpy.ops.angelo.return_dock()


def fail_relocate(*args, **kwargs):
    if False:
        yield None
    raise RuntimeError("intentional regression fixture: docking unavailable")


def tick():
    global subtype_index, area_pointer
    try:
        assert time.monotonic() - started < 90, "Regression fixture timed out"
        phase = state["phase"]
        if phase == "boot":
            ui.setup_chat_area(main, area())
            sessions["a"] = ui.get_controller().state["id"]
            line = area().spaces.active.history[-1]
            line.body, line.current_character = "Private draft A 🧊", 6
            state["checks"]["native_input_accessor_matches_visible_line"] = area().spaces.active.coding_agent_input.as_pointer() == line.as_pointer()
            select("CONSOLE")
            region = next(r for r in area().regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=main, area=area(), region=region):
                if not area().spaces.active.history:
                    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
            area().spaces.active.history[-1].body = "print('keep native Python draft')"
            area().spaces.active.history[-1].current_character = 7
            ui.create_session()
            sessions["b"] = ui.get_controller().state["id"]
            state["checks"]["hidden_native_draft_cleared_for_new_chat"] = area().spaces.active.coding_agent_input.body == ""
            state["checks"]["session_switch_leaves_python_input_and_cursor_unchanged"] = (area().spaces.active.history[-1].body, area().spaces.active.history[-1].current_character) == ("print('keep native Python draft')", 7)
            select("CODING_AGENT")
            state["checks"]["new_chat_never_shows_previous_chat_draft"] = area().spaces.active.history[-1].body == ""
            area().spaces.active.history[-1].body = "Private draft B"
            area().spaces.active.history[-1].current_character = 5
            select("GENERATIVE_AI")
            region = next(r for r in area().regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=main, area=area(), region=region):
                if not area().spaces.active.history:
                    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
            area().spaces.active.history[-1].body = "Keep generation prompt"
            ui.show_session(sessions["a"])
            state["checks"]["generation_prompt_not_overwritten_by_chat_switch"] = area().spaces.active.history[-1].body == "Keep generation prompt"
            select("CODING_AGENT")
            state["checks"]["hidden_original_draft_and_cursor_restored"] = (area().spaces.active.history[-1].body, area().spaces.active.history[-1].current_character) == ("Private draft A 🧊", 6)
            select("PROPERTIES")
            ui.show_session(sessions["b"])
            select("CODING_AGENT")
            state["checks"]["inactive_console_space_switches_to_its_session_draft"] = area().spaces.active.history[-1].body == "Private draft B"
            main.workspace = bpy.data.workspaces["Modeling"]
            state["phase"] = "hidden_workspace"
        elif phase == "hidden_workspace":
            ui.show_session(sessions["a"])
            main.workspace = layout_workspace
            state["phase"] = "workspace_restored"
        elif phase == "workspace_restored":
            state["checks"]["inactive_workspace_draft_is_conversation_scoped"] = area().spaces.active.history[-1].body == "Private draft A 🧊"
            state["checks"]["other_conversation_draft_remains_recoverable"] = ui._drafts[sessions["b"]] == ("Private draft B", 5)
            state["phase"] = "subtype"
        elif phase == "subtype":
            if subtype_index == len(subtypes):
                area().spaces.active.history[-1].body = "Return draft before edit"
                area().spaces.active.history[-1].current_character = 4
                area().spaces.active.font_size = 21
                state["fallback_draft_before_popout"] = area().spaces.active.history[-1].body
                popout()
                _, source = popped_chat()
                state["fallback_draft_immediately_after_popout"] = source.spaces.active.history[-1].body
                main.workspace = bpy.data.workspaces["Modeling"]
                state["phase"] = "fallback_failure_start"
            else:
                identity, mode = subtypes[subtype_index]
                select(identity)
                if mode:
                    setattr(area().spaces.active, "ui_mode" if identity == "IMAGE_EDITOR" else "mode", mode)
                ui.setup_chat_area(main, area())
                popout()
                state["phase"] = "subtype_popped"
        elif phase == "subtype_popped":
            identity, mode = subtypes[subtype_index]
            state["checks"]["restored_" + identity + ("_" + mode if mode else "")] = area().ui_type == identity
            if mode:
                state["checks"]["retained_mode_" + mode] = getattr(area().spaces.active, "ui_mode" if identity == "IMAGE_EDITOR" else "mode") == mode
            assert 'FINISHED' in return_popout()
            subtype_index += 1
            state["phase"] = "subtype"
        elif phase == "fallback_failure_start":
            _, source = popped_chat()
            state["fallback_source_before"] = {"windows": len(bpy.context.window_manager.windows), "draft": source.spaces.active.history[-1].body}
            state["checks"]["workspace_change_requires_fallback_dock"] = not any(a.as_pointer() == ui._dock_area_pointer for a in main.screen.areas)
            docking.relocate_chat = fail_relocate
            assert 'FINISHED' in return_popout()
            state["checks"]["queued_return_does_not_close_source_early"] = len(bpy.context.window_manager.windows) == 2
            state["phase"] = "fallback_failed"
        elif phase == "fallback_failed":
            if not qa_boot.started():
                return .1
            docking.relocate_chat = original_relocate
            _, chat = popped_chat()
            state["fallback_source_after_failure"] = {"windows": len(bpy.context.window_manager.windows), "draft": chat.spaces.active.history[-1].body}
            state["checks"]["failed_return_keeps_popout_and_original_draft"] = len(bpy.context.window_manager.windows) == 2 and chat.spaces.active.history[-1].body == "Return draft before edit"
            state["checks"]["failed_return_has_a_visible_error"] = any("pop-out was kept open" in m["text"] for m in ui.get_controller().state["messages"])
            assert 'FINISHED' in return_popout()
            ui.create_session()
            state["phase"] = "conversation_changed_during_return"
        elif phase == "conversation_changed_during_return":
            if not qa_boot.started():
                return .1
            _, chat = popped_chat()
            state["checks"]["conversation_change_cancels_transfer_without_closing_source"] = len(bpy.context.window_manager.windows) == 2 and chat.spaces.active.history[-1].body == ""
            state["checks"]["conversation_change_has_a_visible_error"] = any("conversation changed" in m["text"] for m in ui.get_controller().state["messages"])
            ui.show_session(sessions["a"])
            state["checks"]["original_draft_survives_canceled_conversation_transfer"] = (chat.spaces.active.history[-1].body, chat.spaces.active.history[-1].current_character) == ("Return draft before edit", 4)
            assert 'FINISHED' in return_popout()
            state["checks"]["successful_fallback_waits_for_commit_before_closing"] = len(bpy.context.window_manager.windows) == 2
            chat.spaces.active.history[-1].body = "Return latest draft 🧊"
            chat.spaces.active.history[-1].current_character = 8
            chat.spaces.active.font_size = 23
            state["phase"] = "fallback_completed"
        elif phase == "fallback_completed":
            if not qa_boot.started():
                return .1
            restored = next(a for a in main.screen.areas if console.is_chat(a))
            line = restored.spaces.active.history[-1]
            state["checks"]["workspace_fallback_preserves_latest_draft_and_cursor"] = (line.body, line.current_character) == ("Return latest draft 🧊", 8)
            state["checks"]["workspace_fallback_preserves_font_size"] = restored.spaces.active.font_size == 23
            state["checks"]["source_closes_after_successful_transfer"] = len(bpy.context.window_manager.windows) == 1
            state["checks"]["no_provider_started"] = not any(c.provider for c in ui._controllers.values())
            state["checks"]["scene_unchanged"] = scene_before == [(o.name, tuple(o.location)) for o in main.scene.objects]
            state.update(phase="complete", success=all(state["checks"].values()))
            save()
            print("ANGELO_EDITOR_REGRESSIONS", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        save()
        return .3
    except BaseException as error:
        docking.relocate_chat = original_relocate
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


save()
bpy.app.timers.register(tick, first_interval=1)
