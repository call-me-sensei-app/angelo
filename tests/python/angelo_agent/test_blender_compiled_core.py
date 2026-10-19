# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled RNA/buffer/save tests, without enabling any agent or using accounts."""

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
assert "is_coding_agent" in bpy.types.SpaceConsole.bl_rna.properties, "Run the compiled fork, not stock Blender"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-compiled-core-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks = {}
window = bpy.context.window_manager.windows[0]
area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
before = [(o.name, tuple(o.location)) for o in window.scene.objects]


def select(identity):
    with bpy.context.temp_override(window=window, area=area):
        area.ui_type = identity
    assert area.ui_type == identity, (identity, area.ui_type)


def set_line(text):
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        if not area.spaces.active.history:
            bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=False)
        area.spaces.active.history[-1].body = text


def execute_python(text):
    assert area.ui_type == "CONSOLE" and area.spaces.active.language == "python"
    set_line(text)
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.console.execute()


select("CONSOLE")
checks["native_coding_input_accessor_available"] = "coding_agent_input" in bpy.types.SpaceConsole.bl_rna.properties
checks["uninitialized_coding_input_is_null"] = area.spaces.active.coding_agent_input is None
execute_python("angelo_native_namespace_probe = 17; print('NATIVE_PYTHON_17', angelo_native_namespace_probe)")
set_line("PYTHON_INPUT_MUST_STAY_SEPARATE")
area.spaces.active.font_size = 15
checks["native_python_executes"] = any("NATIVE_PYTHON_17 17" in l.body for l in area.spaces.active.scrollback)

select("CODING_AGENT")
checks["distinct_native_editor"] = area.spaces.active.is_coding_agent and area.spaces.active.language == "angelo"
checks["python_input_does_not_enter_agent"] = not area.spaces.active.history
set_line("PRIVATE_AGENT_DRAFT_83bbf49c")
area.spaces.active.font_size = 19
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(window=window, area=area, region=region):
    bpy.ops.console.scrollback_append(text="PRIVATE_AGENT_TRANSCRIPT_7f0a3b91", type="OUTPUT")

select("CONSOLE")
checks["python_buffers_restored"] = area.spaces.active.history[-1].body == "PYTHON_INPUT_MUST_STAY_SEPARATE" and area.spaces.active.font_size == 15
checks["native_accessor_reads_hidden_coding_input"] = area.spaces.active.coding_agent_input.body == "PRIVATE_AGENT_DRAFT_83bbf49c"
area.spaces.active.coding_agent_input.body = "HIDDEN_NATIVE_UPDATE"
checks["hidden_native_update_does_not_touch_python"] = area.spaces.active.history[-1].body == "PYTHON_INPUT_MUST_STAY_SEPARATE"
area.spaces.active.coding_agent_input.body = "PRIVATE_AGENT_DRAFT_83bbf49c"
execute_python("print('NATIVE_NAMESPACE_RETAINED', angelo_native_namespace_probe)")
checks["python_namespace_retained"] = any("NATIVE_NAMESPACE_RETAINED 17" in l.body for l in area.spaces.active.scrollback)
set_line("PYTHON_INPUT_MUST_STAY_SEPARATE")
select("CODING_AGENT")
checks["agent_buffers_restored"] = area.spaces.active.history[-1].body == "PRIVATE_AGENT_DRAFT_83bbf49c" and area.spaces.active.font_size == 19

if "is_generative_ai" in bpy.types.SpaceConsole.bl_rna.properties:
    select("GENERATIVE_AI")
    checks["third_editor_starts_with_separate_buffer"] = not area.spaces.active.history
    set_line("GENERATION_DRAFT_MUST_STAY_SEPARATE")
    select("CONSOLE")
    checks["python_survives_three_way_switch"] = area.spaces.active.history[-1].body == "PYTHON_INPUT_MUST_STAY_SEPARATE"
    select("CODING_AGENT")
    checks["agent_survives_three_way_switch"] = area.spaces.active.history[-1].body == "PRIVATE_AGENT_DRAFT_83bbf49c"
    select("GENERATIVE_AI")
    checks["third_editor_buffer_restored"] = area.spaces.active.history[-1].body == "GENERATION_DRAFT_MUST_STAY_SEPARATE"
    select("CODING_AGENT")

# Native workspace copies also clone cached Console spaces. Copies must own
# their AI lines independently, including the buffer hidden behind Python.
def copied_console():
    workspace = window.workspace.copy()
    space = next(s for screen in workspace.screens for item in screen.areas
                 for s in item.spaces if s.type == "CONSOLE")
    return workspace, space


area.spaces.active.history[-1].current_character = 7
workspace, copied = copied_console()
checks["native_ai_layout_copy_preserves_draft_cursor_and_font"] = (
    copied.history[-1].body, copied.history[-1].current_character, copied.font_size
) == ("PRIVATE_AGENT_DRAFT_83bbf49c", 7, 19)
copied.history[-1].body = "INDEPENDENT_COPY_INPUT"
checks["native_ai_layout_copy_owns_its_input"] = area.spaces.active.history[-1].body == "PRIVATE_AGENT_DRAFT_83bbf49c"
bpy.data.batch_remove(ids=[workspace])
select("CONSOLE")
workspace, copied = copied_console()
checks["native_python_layout_copy_keeps_upstream_empty_history"] = not copied.history
checks["hidden_ai_input_survives_native_layout_copy"] = (
    copied.coding_agent_input.body, copied.coding_agent_input.current_character
) == ("PRIVATE_AGENT_DRAFT_83bbf49c", 7)
copied.coding_agent_input.body = "INDEPENDENT_HIDDEN_COPY"
checks["hidden_ai_layout_copy_owns_its_input"] = (
    area.spaces.active.coding_agent_input.body == "PRIVATE_AGENT_DRAFT_83bbf49c"
    and area.spaces.active.history[-1].body == "PYTHON_INPUT_MUST_STAY_SEPARATE"
)
bpy.data.batch_remove(ids=[workspace])
select("CODING_AGENT")

with bpy.context.temp_override(window=window, area=area):
    result = bpy.ops.screen.space_type_set_or_cycle(space_type="CONSOLE")
checks["python_shortcut_never_cycles_into_agent"] = 'FINISHED' in result and area.ui_type == "CONSOLE" and area.spaces.active.language == "python"
checks["shortcut_preserves_python_input"] = area.spaces.active.history[-1].body == "PYTHON_INPUT_MUST_STAY_SEPARATE"
checks["native_provider_preferences_section"] = "AI_PROVIDERS" in bpy.context.preferences.bl_rna.properties["active_section"].enum_items

python_copy = OUT / "python-layout.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(python_copy), copy=True, compress=False)
checks["python_history_serialization_unchanged"] = b"PYTHON_INPUT_MUST_STAY_SEPARATE" in python_copy.read_bytes()
select("CODING_AGENT")
chat_copy = OUT / "chat-layout.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(chat_copy), copy=True, compress=False)
data = chat_copy.read_bytes()
checks["private_chat_buffers_not_in_project"] = b"PRIVATE_AGENT_DRAFT_83bbf49c" not in data and b"PRIVATE_AGENT_TRANSCRIPT_7f0a3b91" not in data
checks["save_does_not_clear_live_draft"] = area.spaces.active.history[-1].body == "PRIVATE_AGENT_DRAFT_83bbf49c"
checks["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in window.scene.objects]
checks["no_agent_or_account_started"] = "angelo_agent" not in bpy.context.preferences.addons

# Opening the copy also exercises destruction of all native alternate buffers.
bpy.ops.wm.open_mainfile(filepath=str(chat_copy), use_scripts=False)
loaded = next(a for a in bpy.context.window.screen.areas if a.type == "CONSOLE" and a.spaces.active.is_coding_agent)
checks["native_editor_identity_survives_reload"] = loaded.ui_type == "CODING_AGENT"
checks["private_chat_input_not_reloaded"] = not loaded.spaces.active.history
state = {"success": all(checks.values()), "checks": checks, "binary": bpy.app.binary_path, "version": bpy.app.version_string,
         "fixture": "Compiled native RNA and save/load only; no addon, authentication, inference or OS drop events"}
(OUT / "compiled-core.json").write_text(json.dumps(state, indent=2))
print("ANGELO_COMPILED_CORE", json.dumps(state), flush=True)
assert state["success"], state
