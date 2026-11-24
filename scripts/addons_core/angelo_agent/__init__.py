# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Angelo Agent Chat", "author": "Hyperbond Studio Pte. Ltd.", "version": (0, 1, 0),
    "blender": (5, 2, 0), "location": "Window > Angelo Chat; Preferences > AI Providers",
    "description": "Native docked/pop-out agent chat with official local runtimes and approved Angelo tools",
    "category": "Interface",
}


def _stamp_time():
    """Record this file's time as ``addon_utils.enable`` does after its own import. Angelo's start-up layout imports
    the package before enabling it (``opted_out`` in the start-up script), as do the GUI suites and Blender's console,
    and ``enable`` reloads a module without ``__time__`` ("module changed on disk ... reloading"): this file ran twice
    at every start-up. A file changed on disk since this import still differs, and is reloaded as before.
    ``addon_utils`` stamps its ``bl_pkg`` the same way."""
    import os
    try:
        return os.path.getmtime(__file__)
    except OSError:
        return 0


__time__ = _stamp_time()


def __getattr__(name):
    # Blender's glTF exporter looks this up on every enabled add-on; it acts only on Angelo's web exports.
    if name == "glTF2ExportUserExtension":
        from .web_export import WebExportExtension
        return WebExportExtension
    raise AttributeError(name)


def register():
    import os, bpy  # noqa: E401
    if bpy.app.background and os.environ.get("ANGELO_HEADLESS") == "1":   # The headless child registers its own.
        return
    import sys
    from . import qa_environment  # noqa: F401  First: the QA switches as Angelo started (worker_process).
    from . import tls_distrust
    tls_distrust.install()   # First: every TLS connection in the app refuses compromised CA keys (tls_distrust).
    from . import change_clock
    change_clock.prime()   # Windows: which volumes keep coarse times, listed on a thread; no settle waits for it.
    import bpy
    from . import data_migration, product_env, state_migration, version_carry_over
    # Before anything opens the account store, Media Studio library or agent state. An earlier Blender base's state
    # moves first, so the pre-rename check then sees this version's folder as it is.
    data_migration.migrate_on_startup()
    version_carry_over.carry_over_on_startup(bpy.utils.user_resource('CONFIG'))
    state_migration.migrate_on_startup(bpy.utils.user_resource('CONFIG'))
    deprecated = product_env.deprecated_in_use()
    if deprecated:
        print("Angelo: deprecated environment variables in use; rename them to ANGELO_*:", ", ".join(deprecated))
    from . import console, layout_hold, studio, timer_jobs, ui
    if not bpy.app.background:   # Before the chats or the CLI listener open: one Angelo holds the state folder.
        from . import headless_window, headless_window_app   # doc/angelo/mcp_headless_window.rst
        if headless_window_app.active() and headless_window.supported():
            headless_window_app.register()   # This process is an app's hidden-window session (macOS only).
        elif headless_window.supported():   # The person's launch, where hidden sessions exist: every GUI Angelo
            from . import headless_window_lock   # holds the state folder's lock; a hidden session there comes up as
            # this launch's window instead. Elsewhere no hidden session ever holds the folder, so there is no lock.
            try:
                outcome = headless_window_lock.hand_over(ui.state_directory(), bpy.app.binary_path)
            except Exception as error:   # noqa: BLE001  A bad lock file or record never stops the add-on loading.
                outcome = None
                print("Angelo: could not take the state folder's lock:", error)
            if outcome in ("revealed", "kept"):   # One Angelo per state folder: the hidden session's window is the
                print("Angelo: the Angelo already running is this launch's window (%s)." % outcome)   # person's now.
                os._exit(0)
    if sys.platform.startswith("linux") and not bpy.app.background:
        from . import desktop_linux   # Its bus worker starts here, on the main thread at registration, never from a
        desktop_linux.service()       # later caller's thread, which could be a script's (desktop_linux.service).
    timer_jobs.register()   # A load or a quit closes every running timer job before Blender's data goes (timer_jobs).
    layout_hold.register()   # A lock whose timer a load discarded is released.
    sys.modules["_console_angelo"] = console
    sys.modules["_console_angelo_genai"] = studio
    ui.register()
    from . import script_confinement_blender   # Before any agent script: they run confined (script_confinement.rst).
    script_confinement_blender.install_app(bpy)
    script_confinement_blender.register_fixed_folders()   # Before any script region: C refuses it after.
    ui.start_native_checks()   # After install_app: a check's private runtime profile is judged by C's folders.
    from . import wrapped_ops
    wrapped_ops.registry()   # Typed operations' declarations, frozen before any agent script can edit them.
    from . import document_identity
    document_identity.register()   # The document's saved id (save_pre) and a failed load (load_post_fail).
    from . import restored_document   # Only a file under the state folder's checkpoints is a Restore's working copy.
    restored_document.set_state_root(ui.state_directory())
    from . import fab_convert
    fab_convert.register()   # The Fab converter child is this app: its binary, read on the main thread.
    from . import tool_specs
    tool_specs.refresh_worker_count()   # The start tool's count, now that Workers at once is readable (parallel_setting).
    # Registered here, not in ui.py (pinned at its size by test_native_module_size).
    from . import session_rail_input
    session_rail_input.register()   # Concurrent sessions: the session rail and live views (before later overlays).
    from . import about_card, app_notices, font_watch, job_ui, prefs_reload, pycache_retention, settings_import_ui
    prefs_reload.install()   # Reading preferences again (factory, revert, a template) keeps Angelo enabled.
    font_watch.register()    # Rebinds the chat's fonts after Blender reloads its own (a theme preset, Text Rendering).
    app_notices.register()   # Update notices, crash reports and What's New (doc/angelo/updates_and_reports.rst).
    job_ui.register()        # Background jobs: their timer, file events, status bar and operators.
    from . import edit_tool_status
    edit_tool_status.register()   # The status bar names the person's operation Angelo's work waits for.
    from . import refused_saves
    refused_saves.register()   # A save another program keeps refusing (Windows) is told on a card.
    about_card.register()    # The About Angelo card (the Angelo menu's About).
    settings_import_ui.register()   # Import Blender Settings… and its undo (File > Defaults, Preferences).
    pycache_retention.schedule()   # Earlier builds' bytecode caches, a minute after startup, on a thread.
    from . import studio_assets_host
    studio_assets_host.register()   # Media Studio as a Blender asset library (a timer; nothing on the startup path).
    from . import asset_shelf_add, chat_launcher, chat_toggle, media_shelf, studio_toggle
    chat_toggle.register()   # Show or hide the chat from the top bar, or Ctrl+Option+N.
    studio_toggle.register()   # Media Studio from Director's top bar and the Angelo menu.
    from . import ai_render_card, render_engines
    render_engines.register()   # The render engine switcher: Director's top bar, Render > Render Engine (1.0.8).
    ai_render_card.register()   # AI render: the camera's view restyled into Media Studio (1.0.8).
    from . import bake_maps_card
    bake_maps_card.register()   # Bake Maps: every PBR map of the selection as a texture set (long_jobs.rst).
    from . import render_region
    render_region.register()   # A region rendered over the last render (doc/angelo/render_region.rst).
    from . import uv_checker
    uv_checker.register()   # The viewport UV checker's toggle, card and + menu row (doc/angelo/uv_checker.rst).
    from . import rigging_ops
    rigging_ops.register()   # Retarget by role, game rig, root motion, action clips (doc/angelo/rigging.rst).
    from . import ik_controls
    ik_controls.register()   # IK controls and the IK/FK switch on any humanoid, by role (doc/angelo/rigging.rst).
    from . import uv_layout_ui
    uv_layout_ui.register()   # UV Layout: unwrap and pack for one texture set (its page in doc/angelo).
    from . import loops_menu
    loops_menu.register()   # The Loops menu: native LoopTools tools under LoopTools' names (doc/angelo/features.rst).
    from . import shape_cut_tool
    shape_cut_tool.register()   # Shape Cut: draw a shape on a surface to cut it, live (doc/angelo/shape_cut.rst).
    from . import collision_fit
    collision_fit.register()   # Convex collision hulls as Game Physics colliders for every engine (game_physics.rst).
    from . import splat_mesh
    splat_mesh.register()   # Gaussian splats to collision and visual meshes, and splat PLY export (features.rst).
    from . import skeleton_check_ui
    skeleton_check_ui.register()   # Check a rig for a game engine: + menu row and card (doc/angelo/engine_skeleton.rst).
    from . import scatter_ui
    scatter_ui.register()   # Scatter: assets on a surface by rules, as Geometry Nodes (doc/angelo/scatter.rst).
    from . import look_ui
    look_ui.register()   # Camera Look: lens and film look, Render tools row (doc/angelo/camera_look.rst).
    chat_launcher.register()   # Angelo in the 3D view's corner while the chat is hidden; a click opens it.
    asset_shelf_add.register()   # Generate on every Asset Shelf: Media Studio on the shelf's tab.
    media_shelf.register()   # Object mode's Asset Shelf: models and materials, Media Studio's included.
    from . import media_shelf_cover
    media_shelf_cover.register()   # The shelf's samples while the start-up cover hides the 3D views.
    from . import photo_drop
    photo_drop.register()   # A photo dropped on the 3D view: Angelo's drop card (doc/angelo/photo_drop.rst).
    from . import tension_map
    tension_map.register()   # Tension map: stretch and compression on the 3D view (doc/angelo/tension_map.rst).
    from . import retopology_ui
    retopology_ui.register()   # Retopology's row in the + menu's Model group (doc/angelo/retopology.rst).
    from . import markup_ui
    markup_ui.register()   # Mark up a picture for the agent (doc/angelo/attachment_markup.rst).
    from . import shelf_empty
    shelf_empty.register()   # A shelf that lists nothing says so and offers Media Studio.
    from . import export_baked, web_export_ui, web_shader_ui
    export_baked.register()   # Object.angelo_baked: a target's baked material for Angelo's glTF exports (long_jobs.rst).
    web_shader_ui.register()   # Material.angelo_web_shader and the material's Web Shader panel.
    from . import game_physics_ui
    game_physics_ui.register()   # Game physics: Properties > Physics and its popup (doc/angelo/game_physics.rst).
    web_export_ui.register()   # File > Export > Web (.glb, Three.js) and Live Web Preview (doc/angelo/web_export.rst).
    from . import shot_capture_ui
    shot_capture_ui.register()   # Make Video from This Shot: Render menu, camera menu, chat + menu (long_jobs.rst).
    from . import engine_link_ui, engine_suggest_ui
    engine_link_ui.register()   # The engine live link and its requests (doc/angelo/engine_link.rst).
    engine_suggest_ui.register()   # Suggests the link once when an engine's editor runs (a timer and a thread).
    from . import library_ui
    library_ui.register()   # The Angelo Library: the 3D view's search pill and browser (doc/angelo/library.rst).
    from . import director_select_through
    director_select_through.register()   # Select Through's + menu row (the header and Director draw their own).
    from . import feedback_ui
    feedback_ui.register()   # Report a bug / Request a feature: the + menu's Feedback rows (app_identity.rst).
    from . import learn_ui
    learn_ui.register()   # Learn: the + menu's page and Teach me as you work (doc/angelo/learn_mode.rst).
    from . import planning_ui
    planning_ui.register()   # Plan mode's Go and Stop planning (doc/angelo/plan_mode.rst).
    from . import context_folder_ui, rules_ui
    context_folder_ui.register()   # Folder as context: the + menu footer (agent_chat.rst).
    rules_ui.register()   # Rules: standing instructions, the + menu footer's page (doc/angelo/agent_chat.rst).


def unregister():
    import os, bpy  # noqa: E401
    if bpy.app.background and os.environ.get("ANGELO_HEADLESS") == "1":   # The headless child registers its own.
        return
    import sys
    from . import (about_card, app_notices, codex_runtime, font_watch, job_ui, prefs_reload, pycache_retention,
                   settings_import_ui, ui)
    from . import asset_shelf_add, chat_launcher, chat_toggle, media_shelf, studio_toggle, web_export_ui, web_shader_ui
    from . import game_physics_ui
    from . import director_select_through, engine_link_ui, engine_suggest_ui, library_ui, shelf_empty, shot_capture_ui
    from . import context_folder_ui, feedback_ui, grant_hints, learn_ui, planning_ui, rules_ui
    planning_ui.unregister()
    grant_hints.unregister()   # No hint or draw handler outlives the add-on.
    rules_ui.unregister()
    context_folder_ui.unregister()
    learn_ui.unregister()
    feedback_ui.unregister()
    director_select_through.unregister()
    engine_suggest_ui.unregister()
    library_ui.unregister()   # Its workers stop; nothing is downloading after the add-on.
    engine_link_ui.unregister()   # Stops the engine link: its server and record never outlive the add-on.
    shot_capture_ui.unregister()
    from . import markup_ui
    markup_ui.unregister()
    from . import retopology_ui
    retopology_ui.unregister()
    from . import tension_map
    tension_map.unregister()
    from . import photo_drop
    photo_drop.unregister()   # Gives Blender's own image drop on the 3D view back.
    web_export_ui.unregister()   # Stops a running web preview: its server never outlives the add-on.
    game_physics_ui.unregister()
    web_shader_ui.unregister()
    from . import export_baked
    export_baked.unregister()
    shelf_empty.unregister()
    from . import media_shelf_cover
    media_shelf_cover.unregister()
    media_shelf.unregister()
    asset_shelf_add.unregister()
    from . import (ai_render_card, bake_maps_card, look_ui, render_engines, render_region, scatter_ui,
                   skeleton_check_ui, uv_checker)
    scatter_ui.unregister()
    look_ui.unregister()
    skeleton_check_ui.unregister()
    bake_maps_card.unregister()
    render_region.unregister()
    uv_checker.unregister()
    from . import ik_controls
    ik_controls.unregister()
    from . import rigging_ops
    rigging_ops.unregister()
    from . import loops_menu
    loops_menu.unregister()
    from . import shape_cut_tool
    shape_cut_tool.unregister()
    from . import splat_mesh
    splat_mesh.unregister()
    from . import collision_fit
    collision_fit.unregister()
    from . import uv_layout_ui
    uv_layout_ui.unregister()
    ai_render_card.unregister()
    render_engines.unregister()
    studio_toggle.unregister()
    chat_launcher.unregister()
    chat_toggle.unregister()
    prefs_reload.uninstall()   # Disabled (by the person, or at exit): Blender's own add-on resets again.
    pycache_retention.cancel()
    from . import studio_assets_host
    studio_assets_host.unregister()
    settings_import_ui.unregister()
    about_card.unregister()
    job_ui.unregister()
    from . import edit_tool_status
    edit_tool_status.unregister()
    from . import refused_saves
    refused_saves.unregister()
    app_notices.unregister()
    font_watch.unregister()
    from . import session_rail_input
    session_rail_input.unregister()
    ui.unregister()
    from . import document_identity
    document_identity.unregister()
    codex_runtime.close_all()  # Shared Codex app-servers outlive their chats until the add-on unloads.
    from . import write_turn
    write_turn.close_all(2.0)   # Then the receipts their warm-ups left: nothing writes into the state folder after.
    from . import layout_hold, timer_jobs
    layout_hold.unregister()
    timer_jobs.unregister()   # Last: each module above unregistered its own timers; the jobs left end, aborted.
    sys.modules.pop("_console_angelo", None)
    sys.modules.pop("_console_angelo_genai", None)
