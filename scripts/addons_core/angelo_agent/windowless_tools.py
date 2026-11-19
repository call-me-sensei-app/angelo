# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tools a Blender without Angelo's window may run for a chat: a worker Blender's part (``worker_refusals``).

Nothing may wait for a person there, so only reads and the Auto lane's bounded typed edits run. Background jobs and
the web export write files, the web preview serves the scene and opens a browser, the engine fixes wait for their
card, the engine link writes into an engine's project, a download and a library import wait for the window's card;
Python and accepting a preview need a person's review. Until 2.1.0 these were also the tools a connected app could
call headless (doc/angelo/external_mcp.rst, "What changed from 2.0.0"); only workers use them now. No bpy.
"""

from .tool_specs import BOUNDED_MUTATIONS, READ_ONLY_TOOLS, TOOL_MAP, WITHHELD_TOOLS

READS = frozenset({
    "angelo_inspect_scene", "angelo_inspect_sculpt", "angelo_scene_search", "angelo_scene_object",
    "angelo_scene_relationships", "angelo_scene_delta", "angelo_scene_verify", "angelo_scene_settings",
    "angelo_read_data", "angelo_blend_file", "angelo_inspect_preview", "angelo_expand_recipe",
    "angelo_blender_api", "angelo_agent_capabilities", "angelo_agent_metrics", "angelo_memory_read",
    "angelo_script", "angelo_view", "angelo_sculpt_view", "angelo_sculpt_review", "angelo_sculpt_target",
    "angelo_web_check", "angelo_engine_check", "angelo_skeleton_check", "angelo_game_ready_check",
    "angelo_engine_link_status", "angelo_library_search",
})
# Typed, bounded scene edits, each one Undo step. Generation Flows are Media Studio work (paid generation), and a
# merge of parallel workers' parts is the lead chat's own (worker_merge).
WINDOW_ONLY = frozenset({"angelo_job_start", "angelo_web_export", "angelo_web_preview", "angelo_engine_fix",
                         "angelo_engine_link", "angelo_library_import", "angelo_download", "angelo_accept_preview"})
EDITS = frozenset(BOUNDED_MUTATIONS - {"angelo_flow_build", "angelo_flow_choose", "angelo_workers_start",
                                       "angelo_workers_merge"} - WINDOW_ONLY)
TOOLS = READS | EDITS

# A tool annotation change must not silently widen what a windowless read allows.
if any(name not in READ_ONLY_TOOLS for name in READS - WITHHELD_TOOLS):
    raise RuntimeError("Windowless reads may contain only read-only tools")
if not TOOLS <= set(TOOL_MAP) | WITHHELD_TOOLS:
    raise RuntimeError("A windowless tool names a tool Angelo does not have")
