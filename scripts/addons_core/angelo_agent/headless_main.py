# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker Blender (``worker_main``, inside ``Angelo --background``) sets up before its first load.

It registers only what its calls use (the approval and commit operators, the program handlers, document and scene
tracking; no chat, sign-in, provider, catalog or timer) and keeps the document as a window does (Undo history, saved
state). Until 2.0.1 the MCP launcher's headless child shared it; that child is gone (doc/angelo/external_mcp.rst,
"What changed").
"""

import sys

UNDO_STEPS = 8   # Undo steps a background process keeps: enough for a commit's own step, bounded memory.
APPLY_OPERATORS = ("angelo.apply_approved_tool", "angelo.apply_approved_script")


def apply_operators(ui):
    """The window's own classes for applying an approved tool and an approved script (``ui.CLASSES``): a worker
    dispatches through the same operators, so an approved call runs there exactly as in the window (break-it review
    H1: the script one was missing, so every approved run failed in a real background Blender)."""
    found = {getattr(cls, "bl_idname", ""): cls for cls in ui.CLASSES}
    return tuple(found[name] for name in APPLY_OPERATORS)


def _register(bpy, ui):
    """Only what a worker's calls use; the add-on itself (chat, sign-in, timers) is never enabled here."""
    from . import agent_workspace_ui, native_programs
    from .blender_tools import track_scene_changes
    from .program_storage import retire_native_residue_after_load
    ui.fresh_preference_definitions()   # As imported (preference_definitions).
    for cls in (ui.ANGELO_Preferences, ui.ANGELO_OT_approve) + apply_operators(ui):
        bpy.utils.register_class(cls)
    if ui.__package__ not in bpy.context.preferences.addons:
        bpy.context.preferences.addons.new().module = ui.__package__
    agent_workspace_ui.register()
    native_programs.register()

    @bpy.app.handlers.persistent
    def after_load(_):
        retire_native_residue_after_load(ui.document_key())
    bpy.app.handlers.load_pre.append(ui.document_load_pre)
    bpy.app.handlers.load_post.append(after_load)
    bpy.app.handlers.depsgraph_update_post.append(track_scene_changes)
    from . import script_confinement_blender   # Agent scripts run confined here too (script_confinement.rst).
    script_confinement_blender.install_app(bpy)
    script_confinement_blender.register_fixed_folders()
    import os
    from . import strict_flag   # Before any agent code: the flag of the Angelo that started it (no preferences here).
    if strict_flag.from_environment(os.environ):   # Its own preference says so too, should anything sync it.
        bpy.context.preferences.addons[ui.__package__].preferences.strict_script_protection = True


def _prepare_preferences(bpy):
    """Before the first load (parallel-agent workers share it): no thumbnails; Undo and saved state as in a window."""
    from .program_undo import keep_background_document
    paths = bpy.context.preferences.filepaths
    paths.file_preview_type = 'NONE'   # No thumbnail render on save.
    edit = bpy.context.preferences.edit
    # A native commit needs Global Undo and at least one step; nothing here undoes past a reload, and the person's
    # count (0 refuses every commit, up to 256 holds that many whole-file steps) is theirs to set in the window. The
    # preferences are protected, so neither is ever saved.
    edit.use_global_undo, edit.undo_steps = True, UNDO_STEPS
    # Every load this process makes (its part file, each reload) then starts that document's own Undo
    # history and reads as saved, as a window's load does, and so does every save: native commits (programs,
    # recipes) need its active step, and angelo_blend_file's unsaved_changes is then true only for unsaved edits.
    if not keep_background_document():
        print("Angelo worker: this Angelo keeps no Undo history in background, so staged programs and recipes "
              "cannot apply", file=sys.stderr)
